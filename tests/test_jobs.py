from sepdiff import jobs
from sepdiff.service import Library


def test_queue_order_dedupe_and_position(tmp_path):
    with Library(tmp_path) as lib:
        a = jobs.enqueue(lib.conn, jobs.SCAN, "kant")
        b = jobs.enqueue(lib.conn, jobs.SCAN, "hume")
        assert jobs.enqueue(lib.conn, jobs.SCAN, "kant").id == a.id    # не дублируется
        assert (jobs.position(lib.conn, a), jobs.position(lib.conn, b)) == (0, 1)

        claimed = jobs.claim(lib.conn)
        assert claimed is not None and claimed.id == a.id and claimed.state == "running"
        # `a` — устаревший снимок в состоянии queued: себя в очереди перед собой не считаем
        assert jobs.position(lib.conn, a) == 0
        assert jobs.position(lib.conn, b) == 1


def test_interrupted_jobs_are_requeued(tmp_path):
    with Library(tmp_path) as lib:
        job = jobs.enqueue(lib.conn, jobs.SCAN, "kant")
        jobs.claim(lib.conn)
        jobs.requeue_interrupted(lib.conn)
        assert jobs.get(lib.conn, job.id).state == "queued"  # type: ignore[union-attr]


def test_tick_enqueues_watch_job_only_for_watched(tmp_path):
    worker = jobs.Worker(tmp_path)
    worker.tick_interval = 0
    worker.tick_if_due()
    with Library(tmp_path) as lib:
        assert jobs.active(lib.conn, jobs.WATCH, "all") is None
        lib.set_watched("kant")
    worker.tick_if_due()
    with Library(tmp_path) as lib:
        job = jobs.active(lib.conn, jobs.WATCH, "all")
        assert job is not None and job.kind == jobs.WATCH
