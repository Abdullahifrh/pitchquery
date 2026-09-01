import os
import sys
from rag.engine import ask

SHOW_SQL = os.getenv("RAG_SHOW_SQL", "true").lower() not in ("false", "0", "no")

def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m rag "your question here"')
        raise SystemExit(1)

    question = " ".join(sys.argv[1:])
    result = ask(question)

    print(result.answer)
    if result.sql and SHOW_SQL:
        print(f"\nSQL used:\n{result.sql}")

if __name__ == "__main__":
    main()
