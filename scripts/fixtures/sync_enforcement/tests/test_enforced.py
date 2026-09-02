from lane import fast_lane, slow_lane


def test_the_lanes_agree():
    assert fast_lane() == slow_lane()
