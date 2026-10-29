"""Signal checks from installed plug-ins.

A signal check is a function that takes a company domain and returns a
prospecting.store.Signal: a fact about the company from data you can check, with its source.
Any installed package can provide checks by registering them under the entry-point group
below in its pyproject.toml:

    [project.entry-points."email_agent_send_gate.signals"]
    my_check = "my_package.signals:my_check"

When at least one check is installed, the agent gets a check_signal tool that runs them all.
With none installed, the tool does not exist.
"""

from collections.abc import Callable
from importlib.metadata import entry_points

from prospecting.store import Signal

GROUP = "email_agent_send_gate.signals"
SignalCheck = Callable[[str], Signal]


def load_signal_checks() -> list[SignalCheck]:
    """Every installed check, in name order. A check that fails to load is an error."""
    return [ep.load() for ep in sorted(entry_points(group=GROUP), key=lambda ep: ep.name)]
