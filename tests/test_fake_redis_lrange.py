"""Verifies the FakeRedis fixture supports lrange — required by log-tail tests."""
def test_fake_redis_lrange_basic(fake_redis):
    fake_redis.rpush("mylist", "a", "b", "c", "d", "e")
    assert fake_redis.lrange("mylist", 0, -1) == ["a", "b", "c", "d", "e"]
    assert fake_redis.lrange("mylist", -2, -1) == ["d", "e"]
    assert fake_redis.lrange("mylist", -10, -1) == ["a", "b", "c", "d", "e"]


def test_fake_redis_lrange_unknown_key(fake_redis):
    assert fake_redis.lrange("never-pushed", 0, -1) == []


def test_fake_redis_lrange_positive_range(fake_redis):
    fake_redis.rpush("mylist", "a", "b", "c", "d", "e")
    assert fake_redis.lrange("mylist", 1, 3) == ["b", "c", "d"]
