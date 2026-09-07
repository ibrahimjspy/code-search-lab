"""Real process/HTTP/MCP tests on small synthetic source, without model downloads."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch
from paths import APP
from service import start, request, descriptor, running, stop


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base/'source'; self.root.mkdir()
        (self.root/'calc.py').write_text('def total():\n    return 42\n',encoding='utf-8')
        self.env = patch.dict(os.environ,{'CODESEARCH_CACHE_DIR':str(self.base/'cache')})
        self.env.start()
        self.addCleanup(self.cleanup)
        start(self.root)

    def cleanup(self):
        stop(self.root)
        self.env.stop()
        self.temp.cleanup()

    def test_separate_cli_stop_waits_for_process_exit(self):
        output = subprocess.check_output([sys.executable,str(APP/'codesearch'),'stop',
                                          '--repo',str(self.root),'--json'],text=True,encoding='utf-8')
        self.assertTrue(json.loads(output)['stopped'])
        self.assertFalse(descriptor(self.root).exists())
        # Windows cannot remove this file while the child still owns its handle.
        log = descriptor(self.root).parent/'service.log'
        log.unlink()

    def test_repeated_cli_queries_share_process_and_skip_full_scans(self):
        before = request(self.root,'status')
        for _ in range(2):
            output = subprocess.check_output([sys.executable,str(APP/'codesearch'),'search','total',
                                             '--repo',str(self.root),'--require-service','--json'],text=True,encoding='utf-8')
            result = json.loads(output)
            self.assertEqual(result['route']['mode'],'symbol')
            self.assertFalse(result['freshness']['full_sync'])
        after = request(self.root,'status')
        self.assertEqual(before['full_scans'],after['full_scans'])
        self.assertEqual(before['generation'],after['generation'])

    def test_authenticated_requests_and_origin_rejection(self):
        info = json.loads(descriptor(self.root).read_text())
        url = f"http://127.0.0.1:{info['port']}/rpc"
        for headers in [{}, {'Authorization':'Bearer '+info['token'],'Origin':'https://example.invalid'}]:
            call = urllib.request.Request(url,data=b'{"operation":"status"}',headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(call)
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(RuntimeError): request(self.root,'query',{'query':'x','file':'../outside.py'})

    def test_explicit_sync_and_parse_failure_recovery(self):
        (self.root/'calc.py').write_text('def updated(): return 9',encoding='utf-8')
        result = request(self.root,'query',{'query':'updated','sync_paths':['calc.py']})
        self.assertEqual(result['results'][0]['symbol'],'updated')
        (self.root/'calc.py').write_text('def broken(:',encoding='utf-8')
        with self.assertRaises(RuntimeError): request(self.root,'query',{'query':'updated','sync_paths':['calc.py']})
        (self.root/'calc.py').write_text('def recovered(): return 3',encoding='utf-8')
        self.assertEqual(request(self.root,'query',{'query':'recovered','sync_paths':['calc.py']})['results'][0]['symbol'],'recovered')
        with self.assertRaises(RuntimeError): request(self.root,'read',{'file':'../outside.py'})

    def test_mcp_handshake_and_real_tool_call(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def run():
            parameters = StdioServerParameters(command=sys.executable,
                args=[str(APP/'codesearch'),'mcp','--repo',str(self.root)],env=dict(os.environ))
            async with stdio_client(parameters) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertIn('search_code',[tool.name for tool in tools.tools])
                    result = await session.call_tool('search_code',{'query':'total','mode':'bm25'})
                    self.assertFalse(result.isError)
                    payload = json.loads(result.content[0].text)
                    self.assertEqual(payload['results'][0]['symbol'],'total')
        asyncio.run(run())


if __name__=='__main__': unittest.main()
