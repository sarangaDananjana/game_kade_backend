"""Profile tasks are derived from the shared user's and rider's saved details."""

import re


def normalize_contact(value):
    value = value.strip()
    if not value:
        return ''
    if not re.fullmatch(r'\+?[0-9 ()-]+', value):
        raise ValueError('Enter a valid emergency phone number.')
    digits = re.sub(r'\D', '', value)
    if not 7 <= len(digits) <= 15:
        raise ValueError('Enter an emergency phone number with 7 to 15 digits.')
    return ('+' if value.startswith('+') else '') + digits


def profile_completion(profile):
    try:
        contact_complete = bool(normalize_contact(profile.emergency_contact or ''))
    except ValueError:
        contact_complete = False
    tasks = [
        {'id': 'name', 'title': 'Your name', 'description': 'The name the kitchen and customers will see.',
         'is_complete': bool((profile.user.name or '').strip())},
        {'id': 'vehicle', 'title': 'Vehicle details', 'description': 'Your vehicle type and registration number.',
         'is_complete': profile.vehicle_type == 'bicycle' or bool((profile.vehicle_registration or '').strip())},
        {'id': 'emergency', 'title': 'Emergency contact', 'description': 'A phone number we can use in an emergency.',
         'is_complete': contact_complete},
    ]
    completed = sum(task['is_complete'] for task in tasks)
    return {'total_tasks': len(tasks), 'completed_tasks': completed, 'remaining_tasks': len(tasks) - completed,
            'is_complete': completed == len(tasks), 'tasks': tasks}
