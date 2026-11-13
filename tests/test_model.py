"""The loopback model client and the scripted fake. Offline: HTTP goes to a
local test server on 127.0.0.1. Everything here is synthetic."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from examinator.model import client as client_module
from examinator.model.client import Client, ModelError, probe, strip_fence, unavailable
from examinator.model.fake import Call, FakeModel
from examinator.std.prompts import PREAMBLE

SENTINEL_PROMPT = "ZEBRA-PROMPT-SENTINEL-7391"
SENTINEL_REPLY = "QUOKKA-REPLY-SENTINEL-2846"


def reply(content="OK", finish='stop', tokens=3, model='test-model'):
    return 200, {'model': model, 'choices': [{'finish_reason': finish,
                              'message': {'role': 'assistant', 'content': content}}],
                 'usage': {'completion_tokens': tokens}}


class Server:
    """A local HTTP server answering from a queue of `(status, body)` or
    `(status, body, headers)`; the last entry repeats. Records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []          # (method, path, parsed JSON body or None)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b''
                outer.requests.append((self.command, self.path, json.loads(raw) if raw else None))
                entry = outer.responses.pop(0) if len(outer.responses) > 1 else outer.responses[0]
                status, body, headers = (*entry, {})[:3]
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def serve():
    servers = []

    def start(*responses):
        servers.append(Server(*responses))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def make(server, **options):
    return Client(server.url, 'test-model', retry_delays=(0, 0), **options)


# --- URL validation -----------------------------------------------------------

@pytest.mark.parametrize('url', [
    'http://localhost:8241/v1', 'http://127.0.0.1:8241/v1', 'http://[::1]:8241/v1',
    'https://localhost/v1', 'http://localhost:8241/v1/',
])
def test_loopback_urls_accepted(url):
    assert Client(url, 'm').base_url == url.rstrip('/')


@pytest.mark.parametrize('url', [
    'http://example.com/v1', 'http://192.168.1.5:8241/v1', 'http://0.0.0.0:8241/v1',
    'http://127.0.0.2:8241/v1', 'http://localhost.example.com/v1',
    'http://127.0.0.1.example.com/v1', 'http://[::2]:8241/v1',
    'http://user:pass@localhost:8241/v1', 'http://user@localhost:8241/v1',
    'http://localhost@example.com/v1', 'http://@localhost:8241/v1',
    'ftp://localhost/v1', 'file:///etc/hosts', 'ws://localhost:8241/v1',
    'localhost:8241/v1', '', 'http:///v1', 'http://localhost:port/v1',
    'http://localhost:8241/v1?next=http://example.com',
])
def test_other_urls_rejected(url):
    with pytest.raises(ValueError):
        Client(url, 'm')
    assert probe(url) is None


# --- transport ----------------------------------------------------------------

def test_request_shape(serve):
    server = serve(reply("fine"))
    image = Image.new('RGB', (8, 6), 'white')
    out = make(server, max_tokens=77).complete("Do the task.", images=[image], text="some data",
                                               tag=('s', 'q', 'step'))
    assert out == "fine"
    (method, path, body), = server.requests
    assert (method, path) == ('POST', '/v1/chat/completions')
    assert body['model'] == 'test-model'
    assert body['temperature'] == 0
    assert body['max_tokens'] == 77
    system, user = body['messages']
    assert system == {'role': 'system', 'content': PREAMBLE}
    assert user['role'] == 'user'
    prompt_part, text_part, image_part = user['content']
    assert prompt_part == {'type': 'text', 'text': "Do the task."}
    assert text_part['type'] == 'text' and "some data" in text_part['text']
    assert text_part['text'] != "some data"          # wrapped in a labelled block
    assert image_part['type'] == 'image_url'
    assert image_part['image_url']['url'].startswith('data:image/jpeg;base64,')
    assert 'tag' not in body


def test_reasoning_off(serve):
    server = serve(reply())
    make(server).complete("p")
    make(server, extra_body={'a': 1}).complete("p")
    off, custom = (r[2] for r in server.requests)
    assert off['reasoning_effort'] == 'none'
    assert off['chat_template_kwargs'] == {'enable_thinking': False}
    assert custom['a'] == 1 and 'chat_template_kwargs' not in custom


def test_redirect_refused(serve):
    target = serve(reply("from the redirect target"))
    server = serve((302, {}, {'Location': target.url + '/chat/completions'}))
    with pytest.raises(ModelError, match="302"):
        make(server).complete("p")
    assert len(server.requests) == 1
    assert target.requests == []
    assert probe(server.url) is None


@pytest.mark.parametrize('status', [500, 503])
def test_server_errors_retried_twice(serve, status):
    server = serve((status, {'error': SENTINEL_REPLY}))
    with pytest.raises(ModelError, match=str(status)) as e:
        make(server).complete(SENTINEL_PROMPT)
    assert len(server.requests) == 3
    assert SENTINEL_REPLY not in str(e.value) and SENTINEL_PROMPT not in str(e.value)


def test_retry_recovers_and_waits(serve, monkeypatch):
    slept = []
    monkeypatch.setattr(client_module.time, 'sleep', slept.append)
    server = serve((503, {}), (500, {}), reply("third time"))
    c = Client(server.url, 'test-model')
    assert c.complete("p") == "third time"
    assert slept == [10, 20]
    assert len(server.requests) == 3


@pytest.mark.parametrize('status', [400, 404, 429, 502])
def test_other_errors_fail_at_once(serve, status):
    server = serve((status, {'error': SENTINEL_REPLY}))
    with pytest.raises(ModelError, match=str(status)) as e:
        make(server).complete(SENTINEL_PROMPT)
    assert len(server.requests) == 1
    assert SENTINEL_REPLY not in str(e.value)


def test_unreachable_server_is_a_model_error(serve):
    server = serve(reply())
    url = server.url
    server.close()
    with pytest.raises(ModelError):
        Client(url, 'm', retry_delays=(0, 0), timeout=2).complete("p")
    assert probe(url) is None


@pytest.mark.parametrize('response', [
    reply(SENTINEL_REPLY, finish='length'),
    reply(SENTINEL_REPLY, finish=None),
    reply(SENTINEL_REPLY, finish=SENTINEL_REPLY),
    reply(""),
    reply("  \n"),
    reply(None),
    reply("```json\n```"),
    (200, {'choices': []}),
    (200, {'unexpected': SENTINEL_REPLY}),
    (200, SENTINEL_REPLY.encode()),
])
def test_bad_responses_raise_without_text(serve, response):
    server = serve(response)
    with pytest.raises(ModelError) as e:
        make(server).complete(SENTINEL_PROMPT, text=SENTINEL_PROMPT, expect='json')
    assert len(server.requests) == 1
    message = str(e.value)
    assert SENTINEL_PROMPT not in message and SENTINEL_REPLY not in message
    assert e.value.__cause__ is None and e.value.__suppress_context__ in (True, False)
    assert e.value.__context__ is None or e.value.__suppress_context__


def test_failure_message_is_structural(serve):
    server = serve(reply(SENTINEL_REPLY, finish='length', tokens=4096))
    with pytest.raises(ModelError) as e:
        make(server).complete("p")
    message = str(e.value)
    assert "'length'" in message and "4096" in message and str(len(SENTINEL_REPLY)) in message


def test_expect_strips_one_fence(serve):
    fenced = '```json\n{"a": 1}\n```'
    server = serve(reply(fenced))
    c = make(server)
    assert c.complete("p") == fenced
    assert c.complete("p", expect='json') == '{"a": 1}'
    assert c.complete("p", expect='code') == '{"a": 1}'


def test_inline_reasoning_dropped(serve):
    server = serve(reply("<think>\nhmm\n</think>\n\nOK"))
    assert make(server).complete("p") == "OK"


def test_proxy_environment_ignored(serve, monkeypatch):
    for name in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.setenv(name, 'http://127.0.0.1:9')      # nothing listens there
    for name in ('NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(name, raising=False)
    server = serve(reply("direct"))
    assert make(server).complete("p") == "direct"
    assert len(server.requests) == 1


@pytest.mark.parametrize('answered', ['other-model', None, 7, SENTINEL_REPLY + " x"])
def test_reply_from_another_model_refused(serve, answered):
    """A server that answers with whatever model it has loaded is refused: the
    reply must name the model asked for. The error is structural."""
    server = serve(reply(SENTINEL_REPLY, model=answered))
    with pytest.raises(ModelError, match="another model") as e:
        make(server).complete(SENTINEL_PROMPT)
    assert SENTINEL_PROMPT not in str(e.value) and SENTINEL_REPLY not in str(e.value)
    assert ("'other-model'" in str(e.value)) == (answered == 'other-model')


def test_unavailable_says_why(serve):
    server = serve((200, {'data': [{'id': 'alpha'}]}))
    assert unavailable(server.url, 'alpha') is None
    assert "not listed" in unavailable(server.url, 'beta')
    url = server.url
    server.close()
    assert "model request failed" in unavailable(url, 'alpha')
    assert "localhost" in unavailable('http://example.com/v1', 'alpha')


def test_cli_says_why_the_model_is_unavailable(serve, capsys, tmp_path):
    import argparse
    from examinator.cli import open_model
    server = serve(reply())
    url = server.url
    server.close()
    assert open_model(argparse.Namespace(fake=False, model_url=url, model='m'), tmp_path) is None
    assert "model unavailable: model request failed" in capsys.readouterr().err


# --- strip_fence --------------------------------------------------------------

@pytest.mark.parametrize('text, expected', [
    ('```json\n{"a": 1}\n```', '{"a": 1}'),
    ('```\nx = 1\ny = 2\n```', 'x = 1\ny = 2'),
    ('\n```python\nx = 1\n```\n  ', 'x = 1'),
    ('```c++\nint x;\n```', 'int x;'),
    # nested or double fences lose only the outer one
    ('```json\n```json\n{"a": 1}\n```\n```', '```json\n{"a": 1}\n```'),
    ('```\n```\nx\n```\n```', '```\nx\n```'),
])
def test_strip_fence_strips_one_whole_block(text, expected):
    assert strip_fence(text) == expected


@pytest.mark.parametrize('text', [
    '{"a": 1}',
    'Here you go:\n```json\n{"a": 1}\n```',
    '```json\n{"a": 1}\n```\nHope that helps.',
    'Before\n```\nx\n```\nafter',
    '```\na\n```\nand also\n```\nb\n```',       # two blocks, not one
    '```json {"a": 1} ```',
    '```json\n{"a": 1}',
    '```',
    '',
])
def test_strip_fence_leaves_everything_else(text):
    assert strip_fence(text) == text


# --- probe --------------------------------------------------------------------

def test_probe_lists_models(serve):
    server = serve((200, {'object': 'list', 'data': [{'id': 'alpha'}, {'id': 'beta'}]}))
    assert probe(server.url) == ['alpha', 'beta']
    assert server.requests[0][:2] == ('GET', '/v1/models')
    assert probe(server.url, 'beta') == ['alpha', 'beta']
    assert probe(server.url, 'gamma') is None


@pytest.mark.parametrize('response', [
    (404, {}), (500, {}), (200, b'not json'), (200, {'data': 'nope'}), (200, {'data': [{}]}),
    (200, []), (200, {'data': [{'id': 5}]}),
])
def test_probe_none_for_unusable_endpoint(serve, response):
    server = serve(response)
    assert probe(server.url) is None
    assert len(server.requests) == 1        # no retries when probing


# --- FakeModel ----------------------------------------------------------------

def test_fake_key_precedence():
    fake = FakeModel({
        ('s1', 'q1', 'parse'): "most specific",
        ('q1', 'parse'): "by question",
        'parse': "by step",
        'echo': lambda call: f"{call.tag[0]}:{call.text}",
    })
    assert fake.complete("p", tag=('s1', 'q1', 'parse')) == "most specific"
    assert fake.complete("p", tag=('s2', 'q1', 'parse')) == "by question"
    assert fake.complete("p", tag=('s1', 'q2', 'parse')) == "by step"
    assert fake.complete("p", text="t", tag=('s9', 'q1', 'echo')) == "s9:t"


def test_fake_records_calls():
    fake = FakeModel(lambda call: "ok")
    image = Image.new('RGB', (2, 2))
    fake.complete("first", images=[image], text="data", expect='json',
                  tag=('s', 'q', 'transcribe'))
    fake.complete("second")
    assert fake.calls == [
        Call(('s', 'q', 'transcribe'), "first", "data", (image,), 'json'),
        Call(None, "second", None, (), 'text'),
    ]


def test_fake_missing_script():
    fake = FakeModel({'parse': "x"})
    for tag in [('s', 'q', 'other'), None]:
        with pytest.raises(ModelError, match="no scripted response"):
            fake.complete(SENTINEL_PROMPT, tag=tag)
    with pytest.raises(ModelError, match="no scripted response"):
        FakeModel(lambda call: None).complete("p", tag=('s', 'q', 'parse'))
    assert len(fake.calls) == 2             # failed calls are recorded too


def test_fake_strips_fence_like_the_client():
    fake = FakeModel({'parse': '```json\n{"a": 1}\n```'})
    tag = ('s', 'q', 'parse')
    assert fake.complete("p", expect='json', tag=tag) == '{"a": 1}'
    assert fake.complete("p", expect='code', tag=tag) == '{"a": 1}'
    assert fake.complete("p", tag=tag) == '```json\n{"a": 1}\n```'


# --- live (opt-in) ------------------------------------------------------------

LIVE_URL = 'http://localhost:8241/v1'


@pytest.mark.live
def test_live_tiny_prompt(request):
    """Opt-in: a tiny synthetic text-only prompt to the real loopback server, to
    the one model named by EXAMINATOR_LIVE_MODEL (never any other the server
    lists). Sends no exam material and writes nothing."""
    if 'live' not in (request.config.getoption('markexpr') or ''):
        pytest.skip("live test: run with -m live")
    model = os.environ.get('EXAMINATOR_LIVE_MODEL')
    if not model:
        pytest.skip("live test: name the model in EXAMINATOR_LIVE_MODEL")
    why = unavailable(LIVE_URL, model)
    if why is not None:
        pytest.skip(f"model unavailable: {why}")
    out = Client(LIVE_URL, model, max_tokens=64, timeout=120, retry_delays=()).complete("Reply with the single word OK.")
    assert out.strip()
