import pytest

ray = pytest.importorskip("ray")


def test_distributed_dataset_module_exports():
    from goldenmatch.distributed import dataset
    assert hasattr(dataset, "read_csv_partitioned")
    assert hasattr(dataset, "apply_transforms_distributed")
