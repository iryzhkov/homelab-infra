from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from ansible._internal._datatag._tags import TrustedAsTemplate
import unittest
from pathlib import Path

loader=DataLoader()
play=loader.load_from_file(str(Path(__file__).resolve().parents[3] / 'playbooks/observability-docker-restart.yml'))[0]
tasks=play['tasks'][0]['block']
def task(name):return next(t for t in tasks if t['name']==name)

class ActivationTests(unittest.TestCase):
    def test_capture_excludes_swarm_tasks_with_compose_labels(self):
        template=Templar(loader=loader,variables={'restart_compose_raw':{'stdout_lines':['compose1 ', 'swarm1 service-id', 'compose2 ']}})
        value=template.template(TrustedAsTemplate().tag(task('Exclude Swarm tasks that also carry Compose labels')['ansible.builtin.set_fact']['restart_compose_ids']))
        self.assertEqual(value,['compose1','compose2'])

    def test_native_health_argv_keeps_go_template_literal(self):
        template=Templar(loader=loader,variables={'restart_compose_ids':['compose1']})
        value=template.template(TrustedAsTemplate().tag(task('Require captured containers running and native checks healthy')['ansible.builtin.command']['argv']))
        self.assertEqual(value,['docker','inspect','--format','{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}','compose1'])

    def test_capture_precedes_stop_and_start_follows_private_unlock(self):
        names=[t['name'] for t in tasks]
        self.assertLess(names.index('Capture originally running Compose containers only'),names.index('Stop captured Compose containers before a manager restart'))
        self.assertLess(names.index('Unlock a restarted Swarm manager through the existing secret path'),names.index('Restore only containers that were running before this window'))
        self.assertTrue(play['any_errors_fatal'])

if __name__ == "__main__":
    unittest.main()
