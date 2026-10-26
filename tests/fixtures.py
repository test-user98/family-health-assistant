"""Explicit fictional test data; the application itself never seeds these profiles."""
import json
from health_agent import Agent, CONFIG_PATH


def fixture_config():
    config = json.loads(CONFIG_PATH.read_text())
    config['members'] = {
        'self': dict(name='Alex', relationship='self', aliases=['me', 'myself', 'i'], pronouns=['they', 'them'], age_months=408, facts={}),
        'child': dict(name='Maya', relationship='daughter', aliases=['daughter', 'child'], pronouns=['she', 'her'], age_months=96, facts={}),
        'parent': dict(name='Raj', relationship='father', aliases=['dad', 'father'], pronouns=['he', 'him'], age_months=840, facts={}),
        'infant': dict(name='Ari', relationship='child', aliases=['baby', 'infant'], pronouns=['he', 'him'], age_months=2, facts={}),
    }
    return config


class SeededAgent(Agent):
    def __init__(self, db_path, backend, config=None, clock=None):
        super().__init__(db_path, backend, config if config is not None else fixture_config(), clock)
