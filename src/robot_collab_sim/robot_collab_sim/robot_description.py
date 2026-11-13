"""The robot description, in a form the controller manager can carry.

`gazebo_ros2_control` creates the controller manager inside the gzserver
process and hands it the robot description as a **command-line parameter
override**. rcl parses the value of such an override as YAML, and a YAML
plain scalar cannot contain a colon followed by a space: `note: here` is a
mapping, not a string. A URDF is full of colons -- in `package://` paths, in
`<remapping>` rules, and in the comments that document all of it -- and one
bare `: ` is enough for rcl to refuse the whole description.

The refusal is quiet from the outside. The plugin logs one line and carries
on without a controller manager, so the spawners wait forever for a service
that is never going to answer, and the robot's wheels never turn. The symptom
points at the controller manager; the cause is a colon in a comment.

So the description that reaches the nodes has no XML comments. They are
documentation, they are the only part of this robot's description that carries
a bare `: `, and nothing downstream reads them.

`render` fails loudly when a description still cannot be carried, because the
alternative -- a description that is quietly refused -- is a robot that
silently does not move.
"""

from __future__ import annotations

import re
import subprocess
import sys

#: An XML comment, including anything spanning several lines.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

#: A colon followed by whitespace: the sequence a YAML plain scalar cannot
#: hold. A colon followed by anything else (`package://`, `a:=b`) is fine.
_UNSAFE = re.compile(r":(?=\s)|:\s*$")


def strip_comments(text: str) -> str:
    """Remove the XML comments, and nothing else.

    The rest of the description is left byte for byte alone: the controller
    manager parses it as URDF, and a description that differs from the one the
    robot was spawned with is a description nobody can debug.
    """
    return _COMMENT.sub("", text)


def unsafe_spots(text: str) -> list[str]:
    """The places where a YAML plain scalar would break.

    Each spot is reported as ``line: the text`` so that the person editing
    the URDF is told where to look instead of being told that "rcl" said no.
    """
    spots: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _UNSAFE.finditer(line):
            start = max(0, match.start() - 30)
            spots.append(f"line {number}: ...{line[start:match.end() + 30].strip()}...")
    return spots


def carries_as_parameter(text: str) -> bool:
    """Whether rcl can carry this description as a parameter override.

    The check is the same one rcl makes, without needing rcl: a YAML plain
    scalar cannot hold a colon followed by whitespace.
    """
    return not unsafe_spots(text)


def render(xacro_path: str) -> str:
    """The robot description, ready to be handed to a node.

    The XML comments come out; everything else is exactly what xacro wrote.
    """
    result = subprocess.run(
        ["xacro", xacro_path],
        check=True,
        capture_output=True,
        text=True,
    )
    description = strip_comments(result.stdout)
    spots = unsafe_spots(description)
    if spots:
        raise SystemExit(
            "the robot description cannot be carried as a parameter override: "
            "a YAML plain scalar cannot hold a colon followed by a space.\n"
            + "\n".join(f"  {spot}" for spot in spots)
            + "\nFix the description, or teach the controller manager to read it "
            "from the topic instead."
        )
    return description


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: robot_description.py XACRO_PATH", file=sys.stderr)
        return 2
    sys.stdout.write(render(arguments[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
