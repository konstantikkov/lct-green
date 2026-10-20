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


def photo_prompts(settings):
    light = {'sunny': 'bright cheerful morning sunshine, clear vivid blue sky, small soft white clouds, warm directional sunlight, inviting premium landscape photography, balanced rich natural colors, welcoming sunny atmosphere',
             'daylight': 'soft natural daylight, pale blue sky with soft clouds, balanced photographic exposure',
             'evening': 'warm low evening sunlight, long soft shadows, delicate blue sky, realistic golden hour photography',
             'overcast': 'bright overcast sky, soft diffuse light, subtle shadows, natural restrained colors'}[settings['lighting']]
    season=settings.get('season','summer')
    vegetation={
        'spring':'springtime, fresh light-green budding foliage, flowering lilac and mock orange where present, fresh grass',
        'summer':'summer, dense natural green leafy crowns, low leafy shrubs, realistic green grass',
        'autumn':'autumn, golden yellow orange and russet foliage, seasonal grass, fallen leaves',
        'winter':'winter, leafless deciduous trees and shrubs with visible woody branches, evergreen foliage only on evergreen species, snow-covered lawns and open ground',
    }[season]
    age=settings.get('ageYears')
    growth=('plants at mature catalog sizes' if age is None else
            f'plantings {age} years after planting; preserve exactly the tree heights and crown widths in the input, do not enlarge young trees')
    architecture = ('realistic existing building surfaces, buildings only at the input silhouettes, ' if settings.get('buildingCount', 0) else 'open landscape with no buildings, no urban skyline, ')
    prompt = ('RAW architectural landscape photograph, professionally photographed real urban landscaping, '
              + vegetation + ', ' + growth + ', physical paving materials, '
              'same camera perspective and object positions as input, ' + architecture + light +
              ('; nearby park trees and open ground matching the selected season, no open agricultural fields, no distant barren horizon, no additional buildings' if settings.get('background','park')=='park' else ''))
    prompt += appearance_prompt(settings)
    negative = ('cartoon, illustration, videogame, unity, low poly, plastic, CGI, oversaturated, fake HDR, '
                'new buildings, extra buildings, skyscrapers, additional paths, extra objects, furniture, '
                'people, rocks, boulders, topiary, clipped spherical trees, green balls, sculpture, text, watermark, distorted architecture, blurry, fantasy')
    if not settings.get('fenceCount',0):negative += ', fence, fences, railing, railings, wire mesh, barriers, balustrade'
    negative += ', desert, arid land, sand dunes, barren landscape, farmland, wheat field, endless empty field, dry yellow grass, prairie'
    negative += ', isolated display model, floating diorama, miniature model, blank studio background'
    if settings.get('background','park')=='park':negative += ', empty horizon'
    if settings['lighting']=='sunny':negative += ', overcast, storm clouds, grey sky, gloomy, desaturated, dull lighting, harsh HDR'
    if not settings.get('buildingCount', 0):negative += ', buildings, houses, urban skyline'
    if season=='winter':negative += ', green deciduous foliage, lush green lawn, summer foliage'
    elif season=='autumn':negative += ', uniformly summer-green foliage, snow'
    else:negative += ', snow, leafless winter trees'
    return prompt,negative
