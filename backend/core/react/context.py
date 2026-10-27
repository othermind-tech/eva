from dataclasses import dataclass, field
from typing import List
from core.types import ReActStep


@dataclass
class ReActContext:
    steps: List[ReActStep] = field(default_factory=list)
    tool_steps: int = 0

    def record(self, step: ReActStep):  
        self.steps.append(step)
