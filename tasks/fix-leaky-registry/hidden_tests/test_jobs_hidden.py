import pytest
from jobs import Job, Scheduler, parse_interval


def test_new_jobs_do_not_share_tags():
    a, b = Job("a", "5m"), Job("b", "5m")
    assert a.tags == ["every-m"] and b.tags == ["every-m"]
    assert a.tags is not b.tags


def test_the_tags_that_were_passed_in_are_not_modified():
    given = ["nightly"]
    job = Job("c", "1h", tags=given)
    assert job.tags == ["nightly", "every-h"]
    assert given == ["nightly"]
    assert job.tags is not given


def test_schedulers_have_their_own_jobs():
    first = Scheduler()
    first.add("only-here", "1m")
    second = Scheduler()
    assert second.names() == []
    second.add("only-here", "1m")  # not a duplicate: another scheduler
    assert first.names() == ["only-here"] and second.names() == ["only-here"]


def test_running_jobs_in_one_scheduler_does_not_affect_another():
    first, second = Scheduler(), Scheduler()
    first.add("job", "1m")
    second.add("job", "1m")
    assert first.run_due(0) == ["job"]
    assert [job.name for job in second.due(0)] == ["job"]


def test_jobs_with_the_same_interval_have_their_own_schedule():
    scheduler = Scheduler()
    a, b = scheduler.add("a", "5m"), scheduler.add("b", "5m")
    a.mark_run(100)
    assert a.next_run == 400
    assert b.next_run == 0


def test_a_new_job_is_due_immediately():
    assert Job("x", "2h").next_run == 0


def test_parse_interval_returns_a_new_dict_every_time():
    first = parse_interval("5m")
    first["seconds"] = 1
    first["extra"] = True
    again = parse_interval("5m")
    assert again == {"unit": "m", "seconds": 300}
    assert again is not first


def test_due_orders_by_priority_then_insertion_and_does_not_change_anything():
    scheduler = Scheduler()
    for name, priority in [("j1", 1), ("j2", 3), ("j3", 1), ("j4", 3)]:
        scheduler.add(name, "5m", priority)
    expected = ["j2", "j4", "j1", "j3"]
    assert [job.name for job in scheduler.due(0)] == expected
    assert [job.name for job in scheduler.due(0)] == expected
    assert scheduler.names() == ["j1", "j2", "j3", "j4"]
    assert all(job.next_run == 0 for job in scheduler.due(0))


def test_run_due_returns_names_in_due_order_and_reschedules():
    scheduler = Scheduler()
    scheduler.add("slow", "10m", priority=1)
    scheduler.add("fast", "1m", priority=2)
    assert scheduler.run_due(0) == ["fast", "slow"]
    assert scheduler.run_due(0) == []
    assert scheduler.run_due(60) == ["fast"]
    assert scheduler.run_due(600) == ["fast", "slow"]


def test_duplicate_names_are_refused_within_one_scheduler():
    scheduler = Scheduler()
    scheduler.add("dup", "1m")
    with pytest.raises(ValueError):
        scheduler.add("dup", "1m")


def test_a_schedulers_history_lists_what_it_added_in_order_and_is_its_own():
    first, second = Scheduler(), Scheduler()
    assert first.history == [] and second.history == []
    first.add("b", "1m")
    first.add("a", "1m")
    second.add("c", "1m")
    assert first.history == ["b", "a"] and second.history == ["c"]
    assert first.history is not second.history
    with pytest.raises(ValueError):
        first.add("a", "1m")
    assert first.history == ["b", "a"]  # a refused job is not recorded
