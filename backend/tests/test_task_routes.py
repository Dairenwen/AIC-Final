from flask import Flask

from app.api.tasks import bp as tasks_blueprint


def test_task_creation_route_accepts_both_trailing_slash_forms():
    app = Flask(__name__)
    app.register_blueprint(tasks_blueprint, url_prefix="/api/v1/tasks")
    adapter = app.url_map.bind("localhost")

    endpoint, _ = adapter.match("/api/v1/tasks", method="POST")
    assert endpoint == "tasks.create_task"
    endpoint, _ = adapter.match("/api/v1/tasks/", method="POST")
    assert endpoint == "tasks.create_task"
