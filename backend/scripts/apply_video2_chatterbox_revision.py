"""Apply the approved Video #2 narration revision through the service layer."""

from __future__ import annotations

import asyncio
import json
import uuid

from app.db.session import session_scope
from app.models.content import ContentProject
from app.services.revise_video2_chatterbox import revise_video2_for_chatterbox

PROJECT_ID = uuid.UUID("01a11afb-aa75-7306-b2a2-e5e7af87b988")


async def main() -> None:
    async with session_scope() as session:
        project = await session.get(ContentProject, PROJECT_ID)
        if project is None:
            raise RuntimeError(f"Project {PROJECT_ID} does not exist")
        script = await revise_video2_for_chatterbox(session, project)
        await session.flush()
        print(
            json.dumps(
                {
                    "project_id": str(project.id),
                    "script_id": str(script.id),
                    "version": script.version,
                    "word_count": script.word_count,
                    "scenes": 5,
                    "project_status": project.status,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
