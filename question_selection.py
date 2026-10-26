"""Pick the next missing question. This does not change the medical rules."""


def choose(missing, topic, config):
    if not missing:
        return None
    if 'red_flags' in missing:
        return 'red_flags'

    settings = config.get('question_selection', {})
    if settings.get('mode', 'fixed') == 'fixed':
        return missing[0]
    if settings.get('mode') != 'adaptive':
        raise ValueError('Unknown question selection mode.')

    priorities = settings.get('topic_priorities', {}).get(topic, {
        'stomach': ['hydration_ok', 'severity', 'duration_hours'],
        'fever': ['duration_hours', 'hydration_ok', 'severity'],
        'headache': ['severity', 'duration_hours', 'hydration_ok'],
        'cough': ['severity', 'duration_hours', 'hydration_ok'],
    }.get(topic, []))
    return next(
        (key for key in priorities if key in missing and key in config['questions']),
        missing[0],
    )
