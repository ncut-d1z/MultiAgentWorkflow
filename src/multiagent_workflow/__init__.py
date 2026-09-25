"""MultiAgentWorkflow package."""

from .c2c import C2CWorkflowConfig, C2CWorkflowResult, run_c2c_workflow
from .orchestrator import WorkflowConfig, WorkflowResult, run_workflow

__all__ = [
    "WorkflowConfig",
    "WorkflowResult",
    "run_workflow",
    "C2CWorkflowConfig",
    "C2CWorkflowResult",
    "run_c2c_workflow",
]
__version__ = "0.2.0"
