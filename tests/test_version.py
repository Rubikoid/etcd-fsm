from importlib.metadata import version

import etcd_fsm


def test_public_version_matches_distribution_metadata() -> None:
    assert etcd_fsm.__version__ == version("etcd-fsm")
