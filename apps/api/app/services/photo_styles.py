"""Constrained appearance controls; LLM text never becomes geometry or code."""
def appearance_prompt(settings):
    palette={'natural':'balanced natural colors','vibrant':'fresh lively greens, rich natural colors',
             'warm':'warm welcoming color palette','cool':'cool clean photographic color palette'}
    surface={'current':'retain the existing surface materials','light_stone':'light natural stone texture on existing paved surfaces',
             'warm_stone':'warm sandstone texture on existing paved surfaces','dark_stone':'dark natural stone texture on existing paved surfaces'}
    finish={'natural':'natural documentary photography','advertising':'polished premium landscape advertising photography, inviting composition, balanced luminous exposure'}
    temperature={'warm':'warm sunlit photographic tone','cool':'cool balanced photographic tone','neutral':'neutral white balance'}
    return '; '+', '.join([palette.get(settings.get('palette'),palette['natural']),
                           surface.get(settings.get('surfaceStyle'),surface['current']),
                           finish.get(settings.get('finish'),finish['natural']),temperature.get(settings.get('temperature'),'neutral white balance')])
