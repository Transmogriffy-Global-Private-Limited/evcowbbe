"""One-time initial migration authoring aid, not a second migration engine.

Reads the frozen source model declarations and emits a REVIEWABLE Django
migration. Normal ongoing evolution: manage.py makemigrations, reviewed in Git.
This script is intentionally limited to the initial simple field syntax.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT/"cow/models.py").read_text()
tree = ast.parse(src)
classes = [node for node in tree.body if isinstance(node,ast.ClassDef)]
timestamps = []
for node in classes:
    if node.name == "Timestamped":
        timestamps = [s for s in node.body if isinstance(s,ast.Assign) and isinstance(s.targets[0],ast.Name)]

class Transform(ast.NodeTransformer):
    def visit_Name(self,node):
        if node.id == "uid":
            return ast.copy_location(ast.Attribute(value=ast.Name(id="uuid",ctx=ast.Load()),attr="uuid4",ctx=ast.Load()),node)
        return node
    def visit_Call(self,node):
        node=self.generic_visit(node)
        if isinstance(node.func,ast.Attribute) and node.func.attr in ("ForeignKey","OneToOneField"):
            if node.args and isinstance(node.args[0],ast.Name):
                node.args[0]=ast.Constant(value="cow."+node.args[0].id.lower())
        return node

out = ["# Generated ONCE for source foundation; never regenerate an applied migration.",
       "import uuid", "from django.db import migrations, models", "from django.db.models import Q, F", "", "class Migration(migrations.Migration):",
       "    initial = True", "    dependencies = []", "    operations = ["]
for node in classes:
    if node.name=="Timestamped" or not any(isinstance(b,ast.Name) and b.id in ("Timestamped",) for b in node.bases) and not any(isinstance(b,ast.Attribute) and b.attr=="Model" for b in node.bases):
        continue
    assignments = [s for s in node.body if isinstance(s,ast.Assign) and isinstance(s.targets[0],ast.Name)]
    if any(isinstance(b,ast.Name) and b.id=="Timestamped" for b in node.bases):
        assignments = timestamps + assignments
    fields=[]
    for s in assignments:
        expr=Transform().visit(ast.fix_missing_locations(s.value))
        ast.fix_missing_locations(expr)
        fields.append(f"('{s.targets[0].id}', {ast.unparse(expr)})")
    options=[]
    for child in node.body:
        if isinstance(child,ast.ClassDef) and child.name=="Meta":
            for s in child.body:
                if isinstance(s,ast.Assign) and isinstance(s.targets[0],ast.Name) and s.targets[0].id=="constraints":
                    options.append(f"'constraints': {ast.unparse(s.value)}")
    out.append("        migrations.CreateModel(")
    out.append(f"            name={node.name!r},")
    out.append("            fields=[")
    for f in fields:
        out.append("                "+f+",")
    out.append("            ],")
    if options:
        out.append("            options={"+", ".join(options)+"},")
    out.append("        ),")
out.append("    ]")
result="\n".join(out)+"\n"
assert len([line for line in out if "migrations.CreateModel(" in line]) >= 24
ast.parse(result)
path=ROOT/"cow/migrations/0001_initial.py"
if path.exists():
    if path.read_text() != result:
        raise SystemExit("Refusing to overwrite an existing initial migration; inspect manually")
    print(f"Verified unchanged {path.relative_to(ROOT)} ({len(result)} bytes)")
else:
    path.write_text(result)
    print(f"Generated {path.relative_to(ROOT)} ({len(result)} bytes)")
