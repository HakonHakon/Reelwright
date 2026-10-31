"""Reference inputs must never be mistaken for generated media."""
from studio import comfy


def test_reference_video_is_not_returned_as_a_take(monkeypatch):
    client = comfy.Comfy('127.0.0.1:8188')
    source = {'filename': 'source.mp4', 'type': 'input'}
    generated = {'filename': 'replacement.mp4', 'type': 'output'}
    history = {'job': {'status': {'completed': True}, 'outputs': {
        'load': {'videos': [source]}, 'save': {'videos': [generated]}}}}

    class Response:
        status_code = 200
        def __init__(self, value):
            self.value = value
        def json(self):
            return self.value

    monkeypatch.setattr(client, 'validate', lambda wf: [])
    monkeypatch.setattr(comfy.requests, 'post', lambda *a, **kw: Response({'prompt_id': 'job'}))
    monkeypatch.setattr(comfy.requests, 'get', lambda *a, **kw: Response(history))
    monkeypatch.setattr(comfy.time, 'sleep', lambda seconds: None)
    result = client.run({})
    assert result['videos'] == [generated]
    assert not result['images']
