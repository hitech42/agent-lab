import pytest
from jobs import Job, Scheduler, parse_interval


def test_parse_interval_units():
    assert parse_interval("90s")["seconds"] == 90
    assert parse_interval("5m") == {"unit": "m", "seconds": 300}
    assert parse_interval(" 2h ")["seconds"] == 7200


@pytest.mark.parametrize("bad", ["", "5", "m", "0s", "5x", "-5m", "1.5h"])
def test_bad_intervals_raise(bad):
    with pytest.raises(ValueError):
        parse_interval(bad)


def test_a_job_has_its_interval_tag():
    assert "every-h" in Job("tagged-job", "3h").tags


def test_tags_given_to_a_job_come_first():
    job = Job("tagged-two", "11s", tags=["nightly"])
    assert job.tags[0] == "nightly" and job.tags[-1] == "every-s"


def test_a_scheduler_runs_what_is_due():
    scheduler = Scheduler()
    scheduler.add("visible-hourly", "1h")
    assert "visible-hourly" in scheduler.run_due(0)
    assert "visible-hourly" not in scheduler.run_due(10)
    assert "visible-hourly" in scheduler.run_due(3600)


def test_higher_priority_runs_first():
    scheduler = Scheduler()
    scheduler.add("visible-low", "13s", priority=1)
    scheduler.add("visible-high", "17s", priority=5)
    order = [name for name in scheduler.run_due(0) if name.startswith("visible-")]
    assert order == ["visible-high", "visible-low"]


def test_a_duplicate_name_is_refused():
    scheduler = Scheduler()
    scheduler.add("visible-dup", "1m")
    with pytest.raises(ValueError):
        scheduler.add("visible-dup", "1m")
