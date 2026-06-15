from goldenmatch.core.cluster_profile import capture_cluster_profile


def test_single_box_when_no_ray_no_descriptor():
    p = capture_cluster_profile(descriptor=None, ray_module=None)
    assert p.present is False
    assert p.source == "single_box"
    assert p.num_nodes == 1


def test_descriptor_path():
    desc = {"num_nodes": 4, "total_cpus": 80, "cluster_mem_gb": 256.0, "driver_mem_gb": 48.0}
    p = capture_cluster_profile(descriptor=desc, ray_module=None)
    assert p.present is True
    assert p.source == "descriptor"
    assert p.num_nodes == 4
    assert p.total_cpus == 80
    assert p.driver_mem_gb == 48.0


def test_probe_path_uses_ray_resources():
    class _FakeRay:
        @staticmethod
        def is_initialized():
            return True

        @staticmethod
        def cluster_resources():
            return {"CPU": 80.0, "memory": 256 * 1024 ** 3}

        @staticmethod
        def nodes():
            return [{"Alive": True}, {"Alive": True}, {"Alive": True}]

    p = capture_cluster_profile(descriptor=None, ray_module=_FakeRay)
    assert p.present is True
    assert p.source == "probe"
    assert p.total_cpus == 80
    assert p.num_nodes == 3
    assert p.cluster_mem_gb == 256.0
