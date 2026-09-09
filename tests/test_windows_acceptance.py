"""Exit-verdict checks; OS observations are fixtures, not Windows acceptance evidence."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import windows_acceptance as acceptance


class ExitChecks(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root/'data').mkdir()
        self.log=self.root/'data'/'application.log'
        self.log.write_text('Normal service shutdown completed\n',encoding='utf-8')
        self.process=Mock(pid=1234);self.process.wait.return_value=0
        self.port=45678
        self.observations=[{'processes':[],'listeners':[]}]
        self.commands=[]
        def run(command,**kwargs):
            self.commands.append(command)
            self.assertTrue(kwargs['check'])
            if command[0]=='pwsh':
                observation=self.observations.pop(0)
                if isinstance(observation,Exception):raise observation
                kwargs['stdout'].write(json.dumps(observation))
            return subprocess.CompletedProcess(command,0)
        self.stack.enter_context(patch.object(acceptance,'EVIDENCE',self.root))
        self.stack.enter_context(patch.object(acceptance.subprocess,'run',side_effect=run))
        self.stack.enter_context(patch.object(acceptance.time,'sleep'))
        self.clock=self.stack.enter_context(patch.object(acceptance.time,'monotonic',return_value=0))
        # Reproduce the original network symptom: the OS-based verdict must not depend on it.
        self.socket=self.stack.enter_context(patch.object(acceptance.socket,'create_connection',side_effect=TimeoutError('timed out')))

    def close(self,label='first'):
        return acceptance.close_normally(self.process,self.root,self.port,label)

    def stages(self):
        file=self.root/'windows-stages.jsonl'
        return [json.loads(line)['name'] for line in file.read_text().splitlines()] if file.exists() else []

    def listening(self):
        # Include an unrelated owner: port reuse must not be mistaken for a closed endpoint.
        return {'processes':[{'ProcessId':9999}], 'listeners':[{'LocalPort':self.port,'OwningProcess':9999,'LocalAddress':'127.0.0.1'}]}

    def test_closed_with_tcp_timeout_uses_saved_os_evidence(self):
        self.assertEqual(self.close()['status'],'closed')
        self.assertIn('first-normal-exit',self.stages())
        self.socket.assert_not_called()
        self.process.wait.assert_called_once_with(timeout=60)
        self.assertEqual(json.loads((self.root/'first-exit-1'/'owned-processes.json').read_text()),{'processes':[],'listeners':[]})
        self.assertIn(f'$_.LocalPort -eq {self.port}',self.commands[-1][-1])

    def test_listener_disappears_within_deadline(self):
        self.observations=[self.listening(),{'processes':[],'listeners':[]}]
        self.close()
        self.assertIn('first-normal-exit',self.stages())
        self.assertTrue((self.root/'first-exit-2'/'owned-processes.json').is_file())

    def test_persistent_listener_is_an_independent_diagnostic(self):
        self.observations=[self.listening()];self.clock.side_effect=[0,21]
        result=self.close()
        self.assertEqual(result['status'],'listening')
        self.assertEqual(result['listeners'][0]['OwningProcess'],9999)
        self.assertIn('first-normal-exit',self.stages())
        self.assertTrue((self.root/'first-exit-1'/'owned-processes.json').is_file())

    def test_os_query_failure_does_not_change_verified_exit(self):
        self.observations=[subprocess.CalledProcessError(1,['pwsh'])]
        result=self.close()
        self.assertEqual(result['status'],'unknown')
        self.assertIn('CalledProcessError',result['error'])
        self.assertIn('first-normal-exit',self.stages())

    def test_query_timeout_allows_recovery_and_its_normal_exit(self):
        self.observations=[subprocess.TimeoutExpired(['pwsh'],30)]
        result=self.close()
        self.assertEqual(result['status'],'unknown')
        self.assertIn('TimeoutExpired',result['error'])
        # The caller can continue to recovery; its own exit evidence is still mandatory.
        self.observations=[{'processes':[],'listeners':[]}]
        self.log.write_text('Normal service shutdown completed\n'*2,encoding='utf-8')
        self.assertEqual(self.close('restart')['status'],'closed')
        self.assertIn('restart-normal-exit',self.stages())

    def test_invalid_evidence_is_not_a_closed_port(self):
        self.observations=[{'processes':[],'listeners':None}]
        result=self.close()
        self.assertEqual(result['status'],'unknown')
        self.assertIn('Invalid',result['error'])
        self.assertIn('first-normal-exit',self.stages())

    def test_nonzero_exit_fails_before_listener_query(self):
        self.process.wait.return_value=1
        with self.assertRaisesRegex(RuntimeError,'returned 1'):self.close()
        self.assertEqual(len(self.commands),1);self.assertEqual(self.stages(),[])

    def test_missing_shutdown_log_fails_before_listener_query(self):
        self.log.write_text('Desktop startup\n',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'shutdown log missing'):self.close()
        self.assertEqual(len(self.commands),1);self.assertEqual(self.stages(),[])

    def test_restart_requires_its_own_shutdown_log(self):
        with self.assertRaisesRegex(RuntimeError,'shutdown log missing'):self.close('restart')
        self.log.write_text('Normal service shutdown completed\n'*2,encoding='utf-8')
        self.close('restart')
        self.assertIn('restart-normal-exit',self.stages())

    def test_live_window_capture_still_requires_host(self):
        with self.assertRaisesRegex(RuntimeError,'Test EXE missing'):
            acceptance.capture_processes(self.process.pid,self.root)


if __name__=='__main__':unittest.main()
