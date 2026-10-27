"""S11: the calls of the adapters to a provider go through ``tools.pinned_http`` (the name is resolved once, every
address is judged and the socket is opened to it). The helper is imported when a call is made, not when this module
loads: ``tools`` imports ``llm``, and the other way round would be a loop."""


def _pinned():
    from ..tools import pinned_http  # noqa: PLC0415
    return pinned_http


def request(method, url, **kwargs):
    return _pinned().request(method, url, **kwargs)


def get(url, **kwargs):
    return _pinned().get(url, **kwargs)


def post(url, **kwargs):
    return _pinned().post(url, **kwargs)


def is_own_network(url):
    """:return: whether ``url`` names this machine or a private network (see ``pinned_http.is_own_network``)"""
    return _pinned().is_own_network(url)
