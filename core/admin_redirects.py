"""Keep bookmarks for the former multi-vendor admin models working."""

from django.http import HttpResponseRedirect

MODEL_APPS = {
    'vendor': 'vendors',
    'product': 'products',
    'order': 'orders',
    'orderlocation': 'orders',
    'deliverydistancetier': 'orders',
    'deliveryzone': 'orders',
}


def legacy_model_admin(request, model, remaining_path=''):
    target = f'/admin/{MODEL_APPS[model]}/{model}/{remaining_path}'
    query = request.META.get('QUERY_STRING')
    if query:
        target += f'?{query}'
    return HttpResponseRedirect(target)
