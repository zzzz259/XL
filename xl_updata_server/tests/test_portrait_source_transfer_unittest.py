import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from server_app.portraits import source_io as io
from server_app.portraits import source_transfer as transfer


def pin():
    raw=json.dumps({'timestamp': 123,'data': [{'name': n,'hash': 'a'*32,'size': 4,'ver': 1} for n in ('Data','Arts')]}).encode()
    return io.pin_source(raw,instance_id='i',source_id='s',cdn_base='https://example.invalid/base',version='123',version_file='version.json')


def required(p):
    return transfer.FetchRequired(p.scope_key,p.pin_digest,p.version,'data_'+'a'*32+'.json','a'*32,4,'category',p.pin_digest)


class DownloadContractTests(unittest.TestCase):
    def test_context_unwind_preserves_exception_identity(self):
        from contextlib import contextmanager
        @contextmanager
        def context():
            try: yield
            finally: pass
        error=required(pin())
        with self.assertRaises(transfer.FetchRequired) as caught:  # noqa: SIM117 - exercise context unwind
            with context(): raise error
        self.assertIs(caught.exception,error)
        with self.assertRaises(AttributeError): error.size=10

    def test_worker_api_exists(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('server_app.portraits.source_download_worker'))

    def test_miss_name_only_and_unreachable_methods(self):
        transport=transfer.miss_transport();transport.validate()
        with self.assertRaises(transfer.CacheMiss) as caught:transport.open('name',timeout=.1)
        self.assertEqual(caught.exception.__dict__,{'remote_name':'name'})
        for fn,args in ((transport.read,(None,1)),(transport.close,(None,))):
            with self.assertRaises(transfer.BoundaryError):fn(*args,timeout=.1)

    def test_enrichment_and_wrong_name(self):
        p=pin();r=required(p)
        for name,error in ((r.remote_name,transfer.FetchRequired),('wrong',ValueError)):
            with patch.object(io,'acquire_blob',side_effect=transfer.CacheMiss(name)),self.assertRaises(error) as caught:
                transfer.acquire_authorized(p,remote_name=r.remote_name,wire_md5=r.wire_md5,size=r.size,provenance=r.provenance,selection_ref=r.selection_ref)
            if error is transfer.FetchRequired:self.assertEqual(caught.exception,r)

    def test_bad_authorization_tuple_rejected(self):
        p=pin();r=required(p)
        for change in ({'scope_key':'b'*64},{'pin_digest':'b'*64},{'size':5},{'remote_name':'other'},{'selection_ref':'b'*64},{'provenance':'unknown'}):
            with self.subTest(change=change),self.assertRaises(ValueError):transfer.validate_fetch(p,replace(r,**change))

    def test_repeated_miss_bounded(self):
        p=pin();r=required(p);calls=[]
        def prepare(p,budget):raise r
        def fetch(p,r,budget):
            calls.append(r)
            return io.VerifiedBlob(p.scope_key,p.pin_digest,r.remote_name,'locator',r.size,r.wire_md5,'b'*64)
        with self.assertRaisesRegex(ValueError,'repeated miss'):
            transfer.replay(p,prepare,fetch,max_fetches=3,max_wire_bytes=12,timeout_seconds=1)
        self.assertEqual(len(calls),1)

    def test_https_headers(self):
        from email.message import Message

        from server_app.portraits.source_download_worker import validate_headers
        for values,ok in (({},True),({'Content-Length':'4'},True),({'Content-Length':'5'},False),({'Content-Encoding':'gzip'},False),({'Transfer-Encoding':'chunked'},False),({'X':'a'*17000},False)):
            headers=Message()
            for k,v in values.items():headers[k]=v
            if ok:validate_headers(headers,4)
            else:
                with self.assertRaises(ValueError):validate_headers(headers,4)

class ProtocolTests(unittest.TestCase):
    def request(self):
        from pathlib import Path

        from server_app.portraits.executor import ResourcePolicy
        from server_app.portraits.source_download_worker import make_request
        root=Path.cwd()
        return make_request(pin(),required(pin()),ResourcePolicy(root/'lease',(root/'guard',),(root,)),root/'wire',root/'attempt')

    def test_request_roundtrip_unknown_and_boundaries(self):
        import copy

        from server_app.portraits.source_download_worker import validate_request
        raw=self.request();p,r,_policy,_paths=validate_request(raw)
        self.assertEqual(p,pin());self.assertEqual(r,required(p))
        mutations=[lambda v:v.update(url='https://other.invalid'),
                   lambda v:v['fetch'].update(size=5),
                   lambda v:v['pin'].update(raw='not base64'),
                   lambda v:v.update(work_root=v['cache_root']),
                   lambda v:v.update(operation_id='a'*64),
                   lambda v:v['policy'].update(poll_seconds=.5)]
        for mutate in mutations:
            value=copy.deepcopy(raw);mutate(value)
            with self.assertRaises(ValueError):validate_request(value)

    def test_https_does_not_follow_redirect_or_accept_wrong_body_headers(self):
        import io as binary
        import ssl
        from email.message import Message

        from server_app.portraits.source_download_worker import https_remote
        for status,encoding in ((302,None),(200,'gzip'),(200,None)):
            class Response:
                def close(self):self.fp.close()
            response=Response();response.status=status;response.headers=Message();response.fp=binary.BytesIO(b'data')
            if encoding:response.headers['Content-Encoding']=encoding
            class Connection:
                closed=False
                def request(self,*args,**kwargs):self.args=args;self.kwargs=kwargs
                def getresponse(self,response=response):return response
                def close(self):self.closed=True
            connection=Connection()
            with patch('http.client.HTTPSConnection',return_value=connection) as factory:
                if status!=200 or encoding:
                    with self.assertRaises(ValueError),https_remote(pin(),required(pin())):pass
                else:
                    with https_remote(pin(),required(pin())) as stream:
                        self.assertEqual(stream.read(4),b'data')
                        with self.assertRaises(ValueError):stream.read(-1)
                    self.assertEqual(connection.args,('GET','/base/'+required(pin()).remote_name))
                    self.assertEqual(factory.call_args.kwargs['context'].verify_mode,ssl.CERT_REQUIRED)
                    self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
                self.assertTrue(connection.closed)
                self.assertTrue(response.fp.closed)

    def test_response_close_failure_still_closes_connection(self):
        from email.message import Message
        from unittest.mock import Mock

        from server_app.portraits.source_download_worker import https_remote
        response=Mock(status=200,headers=Message())
        response.close.side_effect=OSError('close failed')
        connection=Mock()
        connection.getresponse.return_value=response
        with (patch('http.client.HTTPSConnection',return_value=connection),
              self.assertRaises(OSError),https_remote(pin(),required(pin()))):
            pass
        response.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_response_acquisition_error_closes_connection(self):
        from unittest.mock import Mock

        from server_app.portraits.source_download_worker import https_remote
        connection=Mock()
        connection.getresponse.side_effect=OSError('receive failed')
        with (patch('http.client.HTTPSConnection',return_value=connection),
              self.assertRaises(OSError),https_remote(pin(),required(pin()))):
            pass
        connection.close.assert_called_once_with()

if __name__=='__main__':unittest.main()
