"""sashing-ci's public UI: a dashboard of the series runs, each a tree of its builds, their
steps and their logs. Read-only: it only GETs the master's REST API, which the pod alone
reaches. The pages update through htmx: the templates name the fragments to fetch, and
when."""

import collections
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, Response, abort, render_template, request, send_from_directory, url_for

API = os.environ.get('SASHING_API', 'http://localhost:8010/api/v2')
PAGE = 50
DAY = 86400

app = Flask(__name__)
# The styles, scripts and fonts: kept by browsers, unchecked, for 5 minutes. A change
# reaches readers within that time.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 300


# The master's REST API

def api(path, **query):
    """The response at path, opened, or a 404 when the master has nothing there."""
    url = f'{API}/{path}'
    if query:
        url += '?' + urllib.parse.urlencode(query, doseq=True)
    try:
        return urllib.request.urlopen(url, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            abort(404)
        raise


def get(path, **query):
    with api(path, **query) as r:
        return json.load(r)


def series_builds():
    """Every run of the `series` builder, newest first."""
    return get('builders/series/builds', order='-buildid', property='msgid')['builds']


def build(buildid):
    return get(f'builds/{buildid}', property=['msgid', 'series_msgid', 'projectname'])['builds'][0]


# A Trigger step links each build request it made, then, once all its builds are done and
# not always, each build: how a build is placed under its step, by either.
BUILD_URL = re.compile(r'#/builders/(\d+)/builds/(\d+)$')
REQUEST_URL = re.compile(r'#/buildrequests/(\d+)$')


def tree(build):
    """build, with its steps, and under each step the builds it triggered. A step's logs
    are left to its page: asking each step for them would take most of the requests."""
    buildid = build['buildid']
    build['steps'] = [s for s in get(f'builds/{buildid}/steps')['steps'] if not s['hidden']]
    ids = [b['buildid'] for b in get(f'builds/{buildid}/triggered_builds')['builds']]
    # The triggered builds again, with their properties: one request for all.
    triggered = []
    if ids:
        triggered = get('builds', buildid__in=ids, property=['msgid', 'series_msgid'])['builds']
    by_number = {(b['builderid'], b['number']): b for b in triggered}
    # A request retried has a build per try.
    by_request = collections.defaultdict(list)
    for b in sorted(triggered, key=lambda b: b['buildid']):
        by_request[b['buildrequestid']].append(b)
    for step in build['steps']:
        step['builds'] = []
        placed = set()
        for url in step['urls']:
            if m := BUILD_URL.search(url['url']):
                children = [by_number[k]] if (k := (int(m[1]), int(m[2]))) in by_number else []
            elif m := REQUEST_URL.search(url['url']):
                children = by_request[int(m[1])]
            else:
                children = []
            for child in children:
                if child['buildid'] not in placed:
                    placed.add(child['buildid'])
                    step['builds'].append(tree(child))
    return build


def with_trees(runs):
    """The runs, the running ones with their trees, which the page polls. A finished
    run's tree is fetched when it is opened."""
    return [b if b['complete'] else tree(b) for b in runs]


# Results, times and properties, for the templates

RESULTS = {None: 'running', 0: 'success', 1: 'warnings', 2: 'failure', 3: 'skipped',
           4: 'exception', 5: 'retry', 6: 'cancelled'}
# How runs are counted and filtered: the rest of RESULTS is 'other'.
BUCKETS = ['running', 'failure', 'warnings', 'success', 'other']


@app.template_filter()
def result(code):
    return RESULTS.get(code, 'unknown')


def bucket(code):
    r = result(code)
    return r if r in BUCKETS else 'other'


@app.template_filter()
def full_time(t):
    return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(t)) if t else ''


@app.template_filter()
def short_time(t):
    return time.strftime('%b %d %H:%M', time.gmtime(t)) if t else ''


@app.template_filter()
def duration(item):
    if not item['started_at']:
        return ''
    s = int((item['complete_at'] or time.time()) - item['started_at'])
    return f'{s // 3600}h{s // 60 % 60:02}m' if s >= 3600 else f'{s // 60}m{s % 60:02}s'


@app.template_filter()
def prop(build, name):
    return build['properties'].get(name, [None])[0]


# The dashboard's head

def status(runs):
    """The project, who is up, the jobs running and queued, and what was checked: the
    series from runs, the series builds. Without the master, only that it is unreachable."""
    try:
        project = get('projects')['projects'][0]
        workers = get('workers')['workers']
        running = get('builds', complete='false')['builds']
        queued = len(get('buildrequests', claimed='false')['buildrequests'])
        patches = get('builders/patch/builds', property='msgid')['builds']
    except (urllib.error.URLError, OSError):
        return {'up': False}

    # Runs per day over the last 30 days, oldest first, by bucket.
    today = int(time.time() // DAY)
    days = [{'day': (today - 29 + i) * DAY, 'runs': collections.Counter()} for i in range(30)]
    for b in runs:
        age = today - int(b['started_at'] // DAY)
        if 0 <= age < 30:
            days[29 - age]['runs'][bucket(b['results'])] += 1

    busy = {b['workerid'] for b in running}
    for w in workers:
        w['state'] = ('offline' if not w['connected_to']
                      else 'building' if w['workerid'] in busy else 'idle')
    return {
        'up': True,
        'project': project,
        'series': len({prop(b, 'msgid') for b in runs}),
        'patches': len({prop(b, 'msgid') for b in patches}),
        'running': len(running),
        'queued': queued,
        'days': days,
        'month': sum(d['runs'].total() for d in days),
        'peak': max([d['runs'].total() for d in days] + [1]),
        'workers': workers,
    }


# The list of runs: what it shows comes from the query, the page's address

# The columns runs sort by, and the order each starts in.
SORTS = {
    'status': (lambda b: BUCKETS.index(bucket(b['results'])), 'asc'),
    'series': (lambda b: prop(b, 'msgid') or '', 'asc'),
    'run': (lambda b: b['number'], 'desc'),
    'started': (lambda b: b['started_at'] or 0, 'desc'),
    'duration': (lambda b: (b['complete_at'] or time.time()) - (b['started_at'] or time.time()),
                 'desc'),
}


def view():
    """The list's search, filter and order, from the query."""
    a = request.args
    sort = a.get('sort') if a.get('sort') in SORTS else 'started'
    order = a.get('order') if a.get('order') in ('asc', 'desc') else SORTS[sort][1]
    return {'q': a.get('q', '').strip(),
            'status': [s for s in BUCKETS if s in a.getlist('status')],
            'sort': sort, 'order': order}


@app.template_global()
def link(endpoint='index', **changes):
    """The address of the current view, with changes; defaults left out."""
    v = {**view(), **changes}
    if v['order'] == SORTS[v['sort']][1]:
        v['order'] = None
    if v['sort'] == 'started':
        v['sort'] = None
    return url_for(endpoint, **{k: x for k, x in v.items() if x not in ('', None, [])})


@app.template_global()
def toggled(status):
    """The statuses shown, with status added, or removed when it was there."""
    shown = view()['status']
    return [s for s in shown if s != status] if status in shown else shown + [status]


def runs_page(runs, page):
    """The page of runs, among the series builds, the current view selects, and whether
    more follow."""
    v = view()
    found = [b for b in runs
             if v['q'].lower() in (prop(b, 'msgid') or '').lower()
             and (not v['status'] or bucket(b['results']) in v['status'])]
    found.sort(key=SORTS[v['sort']][0], reverse=v['order'] == 'desc')
    return with_trees(found[page * PAGE:(page + 1) * PAGE]), len(found) > (page + 1) * PAGE


# Pages, and the fragments htmx fetches

@app.get('/js/<name>')
def js(name):
    """htmx and its idiomorph extension, which the image fetches."""
    return send_from_directory('/opt/sashing-js', name)


@app.get('/')
def index():
    runs = series_builds()
    rows, more = runs_page(runs, 0)
    return render_template('index.html', status=status(runs), view=view(), runs=rows, page=0,
                           more=more)


@app.get('/status')
def status_fragment():
    return render_template('_status.html', status=status(series_builds()))


@app.get('/runs')
def runs_fragment():
    """The next page of runs, as the list scrolls to its end."""
    page = request.args.get('page', 1, type=int)
    rows, more = runs_page(series_builds(), page)
    return render_template('_runs.html', runs=rows, page=page, more=more)


@app.get('/series/<path:msgid>')
def series(msgid):
    found = [b for b in series_builds() if prop(b, 'msgid') == msgid]
    if not found:
        abort(404)
    return render_template('series.html', msgid=msgid, runs=with_trees(found), more=False)


@app.get('/builds/<int:buildid>/tree')
def tree_fragment(buildid):
    return render_template('_tree.html', b=tree(build(buildid)))


@app.get('/builds/<int:buildid>/node')
def node_fragment(buildid):
    """A running series run's node, polled: 286 once it is complete stops htmx polling."""
    b = tree(build(buildid))
    return render_template('_node.html', b=b), 286 if b['complete'] else 200


# Logs: a step's first at the step's own address, any other by its slug

STEP = '/builds/<int:buildid>/steps/<int:number>'
LOG = STEP + '/logs/<slug>'


def step_logs(buildid, number, slug):
    """The step's logs, and the one asked: slug's, or the first; None when the step has
    none."""
    logs = get(f'builds/{buildid}/steps/{number}/logs')['logs']
    log = next((x for x in logs if slug in (None, x['slug'])), None)
    if slug and not log:
        abort(404)
    return logs, log


def log_path(buildid, number, log):
    return f"builds/{buildid}/steps/{number}/logs/{urllib.parse.quote(log['slug'])}"


def log_lines(buildid, number, log, start):
    """The log's lines from start, as (stream, text)."""
    if log['num_lines'] <= start:
        return []
    content = get(f'{log_path(buildid, number, log)}/contents',
                  offset=start)['logchunks'][0]['content']
    # A stdio log's lines start with their stream: h header, o stdout, e stderr.
    return [(line[0], line[1:]) if log['type'] == 's' else ('o', line)
            for line in content.splitlines()]


@app.get(STEP, defaults={'slug': None})
@app.get(LOG)
def log(buildid, number, slug):
    logs, log = step_logs(buildid, number, slug)
    lines = log_lines(buildid, number, log, 0) if log else []
    return render_template('log.html', build=build(buildid),
                           step=get(f'builds/{buildid}/steps/{number}')['steps'][0],
                           logs=logs, log=log, slug=slug, lines=lines, start=len(lines), end=None)


@app.get(STEP + '/lines', defaults={'slug': None})
@app.get(LOG + '/lines')
def lines_fragment(buildid, number, slug):
    """A running log's lines from `from`, then the next poll, or the step's end."""
    _, log = step_logs(buildid, number, slug)
    if not log:
        abort(404)
    start = request.args.get('from', 0, type=int)
    lines = log_lines(buildid, number, log, start)
    end = get(f'builds/{buildid}/steps/{number}')['steps'][0] if log['complete'] else None
    return render_template('_lines.html', buildid=buildid, number=number, slug=slug, log=log,
                           lines=lines, start=start + len(lines), end=end)


@app.get(STEP + '/raw', defaults={'slug': None})
@app.get(LOG + '/raw')
def raw(buildid, number, slug):
    _, log = step_logs(buildid, number, slug)
    if not log:
        abort(404)
    r = api(f'{log_path(buildid, number, log)}/raw')
    # Always text: an HTML log is the build's output, not a page of this site.
    return Response(iter(lambda: r.read(65536), b''), mimetype='text/plain',
                    headers={'X-Content-Type-Options': 'nosniff'})
