"""Pure incremental planning, with no filesystem or scheduling side effects."""
import copy
from .model import validate_catalog, digest

def plan_changes(previous: dict | None, current: dict) -> list[dict]:
    current=validate_catalog(current)
    if previous is None: return []
    previous=validate_catalog(previous)
    if any(previous[k]!=current[k] for k in ('instance_id','source_id')): raise ValueError('baseline scope mismatch')
    old={(r['character_id'],r['skin_id']):r for r in previous['skins']}
    characters={r['character_id'] for r in previous['skins']}
    jobs=[]
    for row in current['skins']:
        identity=(row['character_id'],row['skin_id'])
        if identity not in old: reason='new_skin' if row['character_id'] in characters else 'new_character'
        elif old[identity]['render_key'] != row['render_key']: reason='changed_inputs'
        else: continue
        job={k:current[k] for k in ('schema_version','instance_id','source_id','version')}
        job.update({k:copy.deepcopy(row[k]) for k in ('character_id','skin_id','render_key','profile')})
        job['reason']=reason
        job['job_id']=digest([job[k] for k in ('instance_id','source_id','version','character_id','skin_id','render_key')])
        jobs.append(job)
    return jobs
