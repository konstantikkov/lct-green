"""Shared offline render budgets; output pixels are separate from diffusion pixels."""

def quality_plan(settings):
    resolution = settings.get('resolution', 512)
    high = resolution >= 1024
    aspect=(settings.get('camera') or {}).get('aspect',512/352)
    squareish=min(aspect,1/aspect)>=.8
    return {'resolution': resolution, 'baseResolution': (576 if squareish else 640) if high else resolution,
            'baseSteps': 28 if high else 24, 'detailResolution': (640 if squareish else 768) if high else 0,
            'detailSteps': 20 if high else 0, 'detailStrength': .2 if high else 0}


def workload(settings):
    p = quality_plan(settings)
    base = (p['baseResolution']/512)**2 * p['baseSteps']/24 * settings.get('strength', .85)/.85
    detail = (p['detailResolution']/512)**2 * p['detailSteps']/24 * p['detailStrength']/.85
    aspect=(settings.get('camera') or {}).get('aspect',512/352)
    return (base + detail)*min(aspect,1/aspect)/(352/512)


def dimensions(size, limit, multiple=8):
    scale = limit/max(size)
    return tuple(max(multiple, round(x*scale/multiple)*multiple) for x in size)
