from collections import Counter
from fastapi import FastAPI, Form
import httpx
import os
import random

FPL_URL = 'https://fantasy.premierleague.com/api'
DRAFT_URL = 'https://draft.premierleague.com/api'

LEAGUE_ID = os.getenv('LEAGUE_ID')
DRAFT_LEAGUE_ID = os.getenv('DRAFT_LEAGUE_ID')

CHIPS_MAPPING = {
    '3xc': 'Triple Captain',
    'bboost': 'Bench Boost',
    'freehit': 'Free Hit',
    'wildcard': 'Wildcard'
}

POSITION_MAPPING = {
    1: 'GKP',
    2: 'DEF',
    3: 'MID',
    4: 'FWD'
}


def player_mapping():
    mapping = {}

    r = httpx.get(f'{FPL_URL}/bootstrap-static/')

    for player in r.json()['elements']:
        mapping[player['id']] = {
            'name': player['web_name'],
            'position': POSITION_MAPPING[player['element_type']]
        }

    return mapping


PLAYER_MAPPING = player_mapping()

app = FastAPI()


@app.post('/slack')
def slack(text: str = Form()):
    text = text.lower().strip()

    if text == 'live':
        res = live()
    elif text.startswith('gw '):
        res = gameweek(int(text.split(' ')[-1]))
    elif text.startswith('totw '):
        res = totw(int(text.split(' ')[-1]))
    elif text == 'draft live':
        res = draft_live()
    elif text.startswith('draft gw '):
        res = draft_gameweek(int(text.split(' ')[-1]))
    else:
        res = 'no comprende'

    return {
        'response_type': 'in_channel',
        'blocks': [
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': res
                }
            }
        ]
    }


def live():
    text = 'Rank  Manager                  GW      TOT\n'
    text += '------------------------------------------\n'

    for manager in league():
        rank = manager['rank_sort']
        last_rank = manager['last_rank']

        if rank < last_rank:
            direction = '↑'
        elif rank > last_rank != 0:  # 0 is new entry to league
            direction = '↓'
        else:
            direction = ' '

        rank = str(rank).rjust(2)
        name = manager['entry_name'].ljust(20)
        gw = str(manager['event_total']).ljust(3)
        tot = str(manager['total']).rjust(4)

        text += f'{rank} {direction}  {name}     {gw}    {tot}\n'

    return f'```{text}```'


def draft_live():
    text = 'Rank  Manager                  GW      TOT\n'
    text += '------------------------------------------\n'

    res = draft_league()

    names = {}

    for entry in res['league_entries']:
        names[entry['id']] = entry['entry_name']

    for manager in res['standings']:
        rank = manager['rank_sort']
        last_rank = manager['last_rank']

        if rank < last_rank:
            direction = '↑'
        elif rank > last_rank:
            direction = '↓'
        else:
            direction = ' '

        rank = str(rank).rjust(2)
        name = names[manager['league_entry']].ljust(20)
        gw = str(manager['event_total']).ljust(3)
        tot = str(manager['total']).rjust(4)

        text += f'{rank} {direction}  {name}     {gw}    {tot}\n'

    return f'```{text}```'


def gameweek(number: int):
    points = {}
    bench_points = {}
    transfers_cost = {}
    chips = {}
    picks = {}
    captains = {}
    captains_points = {}
    player_points = {}

    for player in player_gameweek(number):
        player_points[player['id']] = player['stats']['total_points']

    for manager in league():
        res = manager_gameweek(manager['entry'], number)

        if res.get('picks'):
            picks[manager['entry_name']] = res['picks']

            _captain_id = next(p['element'] for p in res['picks'] if p['is_captain'])

            # look for active captain (or vice-captain) with multiplier (x2 or x3)
            # else defaults to chosen captain (e.g. benched and no vice-captain playing)
            captain_id = next((p['element'] for p in res['picks'] if p['multiplier'] >= 2), _captain_id)

            captains.setdefault(captain_id, []).append(manager['entry_name'])

        if res.get('active_chip'):
            chips.setdefault(res['active_chip'], []).append(manager['entry_name'])

        if 'entry_history' in res:
            points.setdefault(res['entry_history']['points'], []).append(manager['entry_name'])
            bench_points.setdefault(res['entry_history']['points_on_bench'], []).append(manager['entry_name'])
            transfers_cost.setdefault(res['entry_history']['event_transfers_cost'], []).append(manager['entry_name'])

    for captain_id, managers in captains.items():
        captains_points.setdefault(player_points[captain_id], []).append(
            {'managers': managers, 'name': PLAYER_MAPPING[captain_id]['name']}
        )

    best = max(points.keys())
    worst = min(points.keys())
    bench = max(bench_points.keys())
    cost = max(transfers_cost.keys())
    best_captain = max(captains_points.keys())
    worst_captain = min(captains_points.keys())

    text = f":trophy: *Best Round:* {', '.join(points[best])} with {best} points"

    if points[worst] != points[best]:
        text += f"\n\n:pepeworry: *Worst Round:* {', '.join(points[worst])} with {worst} points"

    best_captain_names = []
    best_captain_managers = []
    worst_captain_names = []
    worst_captain_managers = []

    for c in captains_points[best_captain]:
        best_captain_names.append(c['name'])
        best_captain_managers.extend(c['managers'])

    for c in captains_points[worst_captain]:
        worst_captain_names.append(c['name'])
        worst_captain_managers.extend(c['managers'])

    text += f"\n\n:success: *Best Captain:* {', '.join(best_captain_names)} with {best_captain} points chosen by {', '.join(best_captain_managers)}"

    if worst_captain != best_captain:
        text += f"\n\n:drake-no: *Worst Captain:* {', '.join(worst_captain_names)} with {worst_captain} points chosen by {', '.join(worst_captain_managers)}"

    text += f"\n\n:bonk-doge: *Bench Warmer:* {', '.join(bench_points[bench])} with {bench} points"

    if cost > 0:
        text += f"\n\n:money_with_wings: *Big Spender:* {', '.join(transfers_cost[cost])} with {-cost} points"

    for chip, players in chips.items():
        text += f"\n\n:alert: *{CHIPS_MAPPING[chip]}:* {', '.join(players)}"

    return text


def draft_gameweek(number: int):
    points = {}

    for entry in draft_league()['league_entries']:
        history = draft_manager_history(entry['entry_id'])

        gw_points = history['history'][number - 1]['points']
        name = history['entry']['name']

        points.setdefault(gw_points, []).append(name)

    best = max(points.keys())
    worst = min(points.keys())

    text = f":trophy: *Best Round:* {', '.join(points[best])} with {best} points"

    if points[worst] != points[best]:
        text += f"\n\n:pepeworry: *Worst Round:* {', '.join(points[worst])} with {worst} points"

    return text


def totw(gw: int):
    player_points = {}
    picks = set()
    gk = {}
    df = {}
    mid = {}
    fwd = {}

    for player in player_gameweek(gw):
        player_points[player['id']] = player['stats']['total_points']

    for manager in league():
        res = manager_gameweek(manager['entry'], gw)

        for pick in res.get('picks', []):
            if pick['multiplier'] > 0:  # not benched
                picks.add(pick['element'])

    # countah = Counter(picks)
    # most_selected = countah.most_common(1)
    # total = len(countah)
    #
    # print(most_selected, total)

    for player_id in picks:
        bucket = None

        match PLAYER_MAPPING[player_id]['position']:
            case 'GKP':
                bucket = gk
            case 'DEF':
                bucket = df
            case 'MID':
                bucket = mid
            case 'FWD':
                bucket = fwd

        bucket[PLAYER_MAPPING[player_id]['name']] = player_points[player_id]

    formations = ['3-4-3', '3-5-2', '4-3-3', '4-4-2', '4-5-1', '5-2-3', '5-3-2', '5-4-1']
    teams = {}

    for formation in formations:
        t = team(gk, df, mid, fwd, formation)
        teams[t['points']] = t

    best_team_points = max(teams.keys())
    best_team = teams[best_team_points]

    text = f"GK: {best_team['gk'][0]} ({best_team['gk'][1]})\n\n"

    for p in best_team['df']:
        text += f"DEF: {p[0]} ({p[1]})\n"

    text += '\n'

    for p in best_team['mid']:
        text += f"MID: {p[0]} ({p[1]})\n"

    text += '\n'

    for p in best_team['fwd']:
        text += f"FWD: {p[0]} ({p[1]})\n"

    return f"```{text}```"


def team(gk: dict, df: dict, mid: dict, fwd: dict, formation: str):
    d, m, f = [int(x) for x in formation.split('-')]

    gk_sorted = sorted(gk.items(), key=lambda p: p[1])
    df_sorted = sorted(df.items(), key=lambda p: p[1])
    mid_sorted = sorted(mid.items(), key=lambda p: p[1])
    fwd_sorted = sorted(fwd.items(), key=lambda p: p[1])

    gk_chosen = gk_sorted.pop()
    df_chosen = [df_sorted.pop() for _ in range(d)]
    mid_chosen = [mid_sorted.pop() for _ in range(m)]
    fwd_chosen = [fwd_sorted.pop() for _ in range(f)]

    gk_points = gk_chosen[1]
    df_points = sum(p[1] for p in df_chosen)
    mid_points = sum(p[1] for p in mid_chosen)
    fwd_points = sum(p[1] for p in fwd_chosen)

    points = gk_points + df_points + mid_points + fwd_points

    return {
        'points': points,
        'gk': gk_chosen,
        'df': df_chosen,
        'mid': mid_chosen,
        'fwd': fwd_chosen
    }


def league():
    r = httpx.get(f'{FPL_URL}/leagues-classic/{LEAGUE_ID}/standings/')
    return r.json()['standings']['results']


def manager_gameweek(manager_id: int, gw: int):
    r = httpx.get(f'{FPL_URL}/entry/{manager_id}/event/{gw}/picks/')
    return r.json()


def player_gameweek(gw: int):
    r = httpx.get(f'{FPL_URL}/event/{gw}/live/')
    return r.json()['elements']


def draft_league():
    r = httpx.get(f'{DRAFT_URL}/league/{DRAFT_LEAGUE_ID}/details')
    return r.json()


def draft_manager_history(manager_id: int):
    r = httpx.get(f'{DRAFT_URL}/entry/{manager_id}/history')
    return r.json()
