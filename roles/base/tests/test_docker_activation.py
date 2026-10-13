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

    def test_interrupted_local_activation_recovers_without_another_restart(self):
        variables={'restart_default':{'stdout':'local'},'restart_state':{'stat':{'exists':True}}}
        template=Templar(loader=loader,variables=variables)
        def evaluate(expression):
            return template.evaluate_conditional(TrustedAsTemplate().tag(expression))
        self.assertTrue(evaluate(play['tasks'][0]['when'][0]))
        self.assertFalse(evaluate(task('Restart the Docker daemon')['when']))
        self.assertFalse(evaluate(task('Capture originally running Compose containers only')['when']))
        self.assertFalse(evaluate(task('Require household health before this restart')['when']))
        self.assertIn('restart_compose_ids',task('Restore only containers that were running before this window')['ansible.builtin.command']['argv'])

    def test_clean_local_activation_is_unchanged(self):
        template=Templar(loader=loader,variables={'restart_default':{'stdout':'local'},'restart_state':{'stat':{'exists':False}}})
        self.assertFalse(template.evaluate_conditional(TrustedAsTemplate().tag(play['tasks'][0]['when'][0])))

    def test_journal_survives_failure_and_unlock_is_preflighted(self):
        names=[t['name'] for t in tasks]
        self.assertLess(names.index('Persist original IDs before interrupting services'),names.index('Stop captured Compose containers before a manager restart'))
        self.assertGreater(names.index('Clear activation journal only after recovery and backend acceptance'),names.index('Require household health before proceeding to another host'))
        self.assertNotIn('Clear activation journal only after recovery and backend acceptance',[t['name'] for t in play['tasks'][0]['always']])
        preflight=next(t for t in play['pre_tasks'] if t['name']=='Preflight private manager unlock data before any interruption')
        self.assertTrue(preflight['block'][0]['no_log'])
        self.assertTrue(preflight['block'][1]['no_log'])
        self.assertEqual(task('Persist original IDs before interrupting services')['ansible.builtin.copy']['mode'],'0600')

if __name__ == "__main__":
    unittest.main()
