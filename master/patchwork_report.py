import re

from buildbot.process.results import CANCELLED, EXCEPTION, FAILURE, SUCCESS, WARNINGS


STATES = {SUCCESS: 'success', WARNINGS: 'warning', FAILURE: 'fail',
          EXCEPTION: 'warning', CANCELLED: 'warning'}


def request(build, step, *, owner, tests, url):
    """Report a selected test from a triage-triggered build to pw-check."""
    properties = build['properties']
    state = STATES.get(step['results'])
    if (properties.get('owner', [None])[0] != owner or state is None
            or not tests.fullmatch(step['name'])):
        return None
    return {
        'msgid': properties['msgid'][0],
        'series': build['builder']['name'] == 'series',
        'checks': [{
            'context': re.sub(r'[^-a-zA-Z0-9_]', '-', step['name']),
            'state': state,
            'description': step['state_string'],
            'url': f"{url}builds/{build['buildid']}/steps/{step['number']}",
        }],
    }
