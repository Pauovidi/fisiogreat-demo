from typing import Generator
def get_db() -> Generator[None, None, None]:
    try:
        yield None
    finally:
        return