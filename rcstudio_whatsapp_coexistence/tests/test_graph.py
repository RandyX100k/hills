"""The Graph client against a fake `requests.request`: error mapping and paging."""
from unittest.mock import patch

import requests

from odoo.tests import tagged

from odoo.addons.rcstudio_whatsapp_coexistence.tools import graph
from odoo.addons.rcstudio_whatsapp_coexistence.tools.graph import GraphClient, GraphError

from .common import TAGS, WhatsAppCase


class FakeResponse:
    def __init__(self, payload=None, status=200, text=''):
        self._payload = payload
        self.status_code = status
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


@tagged(*TAGS)
class TestGraphClient(WhatsAppCase):

    def _client(self):
        return GraphClient(token='tok', phone_number_id='pnid', waba_id='waba', version='v22.0')

    def _patch_request(self, side_effect=None, return_value=None):
        patcher = patch.object(graph.requests, 'request', side_effect=side_effect, return_value=return_value)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    # ------------------------------------------------------------ error mapping
    def test_error_payload_maps_to_graph_error(self):
        self._patch_request(return_value=FakeResponse({'error': {
            'message': '(#130429) Rate limit hit', 'type': 'OAuthException', 'code': 130429,
            'error_subcode': 2494055, 'fbtrace_id': 'AbCd', 'error_user_msg': 'Slow down',
        }}, status=400))
        with self.assertRaises(GraphError) as ctx:
            self._client().send({'to': '1', 'type': 'text', 'text': {'body': 'x'}})
        err = ctx.exception
        self.assertEqual(err.code, 130429)
        self.assertEqual(err.subcode, 2494055)
        self.assertEqual(err.http_status, 400)
        self.assertEqual(err.fbtrace_id, 'AbCd')
        self.assertEqual(err.user_message, 'Slow down')
        self.assertTrue(err.transient, 'Meta throughput limit is retryable')
        self.assertEqual(str(err), '[130429] Slow down')

    def test_non_transient_error(self):
        self._patch_request(return_value=FakeResponse({'error': {
            'message': 'Unsupported message type', 'code': 131047}}, status=400))
        with self.assertRaises(GraphError) as ctx:
            self._client().mark_read('wamid.x')
        self.assertEqual(ctx.exception.code, 131047)
        self.assertFalse(ctx.exception.transient)

    def test_error_in_200_body_still_raises(self):
        # Graph sometimes answers 200 with an error object; that is still an error
        self._patch_request(return_value=FakeResponse({'error': {'message': 'Bad', 'code': 100}}, status=200))
        with self.assertRaises(GraphError) as ctx:
            self._client().phone_info()
        self.assertEqual(ctx.exception.code, 100)

    def test_http_5xx_without_json_is_transient(self):
        self._patch_request(return_value=FakeResponse(None, status=503))
        with self.assertRaises(GraphError) as ctx:
            self._client().phone_info()
        self.assertIsNone(ctx.exception.code)
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertTrue(ctx.exception.transient)
        self.assertEqual(str(ctx.exception), 'HTTP 503')

    def test_transport_failure_is_graph_error(self):
        self._patch_request(side_effect=requests.ConnectionError('boom'))
        with self.assertRaises(GraphError) as ctx:
            self._client().phone_info()
        self.assertIn('Could not reach Meta', str(ctx.exception))
        self.assertFalse(ctx.exception.transient)

    def test_missing_ids_fail_before_any_request(self):
        mocked = self._patch_request()
        with self.assertRaises(GraphError):
            GraphClient(token='tok').send({'to': '1'})
        with self.assertRaises(GraphError):
            GraphClient(token='tok').list_templates()
        mocked.assert_not_called()

    # ------------------------------------------------------------------ send
    def test_send_returns_message_id_and_posts_meta_shape(self):
        mocked = self._patch_request(return_value=FakeResponse({
            'messaging_product': 'whatsapp', 'contacts': [{'wa_id': '1'}], 'messages': [{'id': 'wamid.42'}]}))
        wa_id = self._client().send({'to': '1', 'type': 'text', 'text': {'body': 'hi'}})
        self.assertEqual(wa_id, 'wamid.42')
        mocked.assert_called_once()
        args, kwargs = mocked.call_args
        self.assertEqual(args[0], 'POST')
        self.assertEqual(args[1], 'https://graph.facebook.com/v22.0/pnid/messages')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok')
        self.assertEqual(kwargs['json']['messaging_product'], 'whatsapp')
        self.assertEqual(kwargs['json']['recipient_type'], 'individual')
        self.assertEqual(kwargs['json']['text'], {'body': 'hi'})

    # ---------------------------------------------------------------- paging
    def test_list_templates_follows_paging_cursor(self):
        pages = [
            FakeResponse({'data': [{'name': 'a'}, {'name': 'b'}],
                          'paging': {'cursors': {'before': 'x', 'after': 'cur1'},
                                     'next': 'https://graph.facebook.com/next'}}),
            FakeResponse({'data': [{'name': 'c'}],
                          'paging': {'cursors': {'before': 'y', 'after': 'cur2'}}}),  # no `next`: last page
        ]
        mocked = self._patch_request(side_effect=pages)
        templates = self._client().list_templates(limit=2)
        self.assertEqual([t['name'] for t in templates], ['a', 'b', 'c'])
        self.assertEqual(mocked.call_count, 2)
        first, second = mocked.call_args_list
        self.assertEqual(first.args[1], 'https://graph.facebook.com/v22.0/waba/message_templates')
        self.assertNotIn('after', first.kwargs['params'])
        self.assertEqual(first.kwargs['params']['limit'], 2)
        self.assertEqual(second.kwargs['params']['after'], 'cur1')

    def test_list_templates_single_page(self):
        mocked = self._patch_request(return_value=FakeResponse({'data': [{'name': 'only'}]}))
        self.assertEqual(len(self._client().list_templates()), 1)
        self.assertEqual(mocked.call_count, 1)
