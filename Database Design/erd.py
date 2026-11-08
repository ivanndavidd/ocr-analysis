"""Build the Contract OCR ERD as a Visual Paradigm project (.vpp) and export it as an image.

Edit SCHEMA below, then run:  python erd.py            (writes "Contract OCR ERD.vpp" and ".png" next to this file)
                               python erd.py --replace  (rebuilds them; edits made in the Visual Paradigm app are lost)

How it works: the schema becomes Visual Paradigm's own XML ("simple" structure, the shapes copied from the
Reimbursement ERD's export), Visual Paradigm's command-line ImportXML turns it into a new .vpp, and
ExportDiagramImage draws the diagram. Both tools ship with Visual Paradigm (Community Edition included) and run
without opening its window. Close the .vpp in the Visual Paradigm app before rebuilding it.
"""

from __future__ import annotations

import argparse
import glob
import random
import shutil
import string
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

HERE = Path(__file__).resolve().parent
NAME = "Contract OCR ERD"


@dataclass
class Col:
    name: str
    type: str  # integer, varchar, char, timestamp, date, float, double, bit
    length: int = 0
    nullable: bool = False
    pk: bool = False
    fk: str | None = None  # "Table.column" this column references
    unique: bool = False


@dataclass
class Table:
    name: str
    x: int
    y: int
    width: int
    cols: list[Col] = field(default_factory=list)


# --- the design -------------------------------------------------------------------------------------------------------
# User 1 → many OCRHeader (one per "Jalankan OCR"); OCRHeader 1 → many OCRContractDetail (one per PDF in that run).
# The same PDF can be read in many runs: documentKey (SHA-256 of "vendor/period/file_name") is indexed, not unique;
# (headerId, documentKey) is unique. Foreign keys go last in each table, as in the Reimbursement ERD.
# OCRContractDetail.status is the one outcome HR acts on, the same grouping as the results page's filter boxes:
#   ready        Siap dipakai: read, nothing to check (checks is empty)
#   needs_check  Perlu dicek: read, at least one value to compare with the PDF (checks says which, and why)
#   failed       Gagal dibaca: not read; the first code in reasons says why (timeout, worker_crashed, not_pdf_magic, ...)
#   cancelled    Dibatalkan: the run was cancelled before this file was read
# It replaces the extractor's decision (auto / review / review_priority); "waiting" and "reading" exist only while a
# run is going and are never stored.
# Runs queue first come first served (OCRHeader.status queued -> running -> done | cancelled; interrupted when the
# backend stopped first). A PDF that a run ahead (queued or running at the same time) also has is read once: the
# later run's row copies that reading and reusedFromHeaderId names the run that read it; its tokens count there.
# result keeps the whole reading as JSON so any user can reopen a past run as the results page showed it. checks holds one note per column for HR to compare with the PDF, so it stays
# empty for failed and cancelled files; reasons holds the machine codes for the whole file, a failure's cause included
# (there is no separate errorCode column).
# Laid out right to left (User on the right) so the tallest table sits on the left and Visual Paradigm's
# "Powered By" mark, drawn in the image's bottom-right corner, lands on empty space.
SCHEMA = [
    Table("User", 800, 40, 210, [
        Col("id", "integer", 10, pk=True),
        Col("email", "varchar", 255, unique=True),  # one row per email, added on first sign-in
        Col("name", "varchar", 255),
        Col("createdAt", "timestamp"),
    ]),
    Table("OCRHeader", 420, 40, 260, [
        Col("id", "integer", 10, pk=True),
        Col("jobId", "char", 36, unique=True),  # uuid in PostgreSQL: the run's id in the website's links and API
        Col("status", "varchar", 20),  # queued | running | done | cancelled | interrupted
        Col("mode", "varchar", 20),  # ocr | full_page (no OCR, whole pages to Claude)
        Col("claude", "bit"),  # boolean: Claude was helping when the run started
        Col("folders", "varchar"),  # JSON list of the chosen folders; up to ~4,000 characters seen, so unbounded
        Col("totalFiles", "integer", 10),
        Col("inputTokens", "integer", 10),
        Col("cacheWriteTokens", "integer", 10),
        Col("cacheReadTokens", "integer", 10),
        Col("outputTokens", "integer", 10),
        Col("startedAt", "timestamp"),
        Col("finishedAt", "timestamp", nullable=True),
        Col("userId", "integer", 10, fk="User.id"),
    ]),
    Table("OCRContractDetail", 40, 40, 260, [
        Col("id", "integer", 10, pk=True),
        Col("documentKey", "varchar", 64),  # SHA-256 (hex) of filePath: the same PDF in every run
        Col("filePath", "varchar", 1000),  # root-relative: vendor/period/file name
        Col("status", "varchar", 20),  # ready | needs_check | failed | cancelled (see the design notes above)
        Col("seconds", "double", nullable=True),  # double precision: a float4 reads 15.7 back as 15.699999809
        Col("vendor", "varchar", 255, nullable=True),
        Col("entity", "varchar", 255, nullable=True),
        Col("employeeName", "varchar", 255, nullable=True),
        Col("nik", "varchar", 16, nullable=True),
        Col("position", "varchar", 100, nullable=True),
        Col("startDate", "date", nullable=True),
        Col("endDate", "date", nullable=True),
        Col("months", "integer", 10, nullable=True),
        Col("signature", "varchar", 10, nullable=True),
        Col("checks", "varchar", nullable=True),  # JSON {column: note}; unbounded
        Col("reasons", "varchar", nullable=True),  # JSON list of codes; unbounded
        Col("result", "varchar", nullable=True),  # JSON: the whole reading (ContractRow); null when not read
        Col("headerId", "integer", 10, fk="OCRHeader.id"),
        Col("reusedFromHeaderId", "integer", 10, nullable=True, fk="OCRHeader.id"),  # the run that read it, if another
    ]),
]

# --- Visual Paradigm details (from the Reimbursement ERD) -------------------------------------------------------------
TYPE_CODE = {"integer": 9, "varchar": 27, "char": 31, "timestamp": 22, "date": 17, "float": 4, "double": 6, "bit": 1}
FILL = "rgb(245, 182, 122)"
ROW, HEADER = 18, 24  # table height = HEADER + ROW per column
ID_CHARS = string.ascii_letters + string.digits + "_"

DIAGRAM_ATTRS = (
    'AlignToGrid="false" AutoFitShapesSize="false" ConnectionPointStyle="0" ConnectorLabelOrientation="0" '
    'ConnectorLineJumps="0" ConnectorLineJumpsSize="0" ConnectorModelElementNameAlignment="4" ConnectorStyle="1" '
    'DataModel="2" DiagramBackground="rgb(255, 255, 255)" Editable="true" FilterByViewpointLegend="true" '
    'FollowDiagramParentElement="true" ForeignKeyArrowHeadSize="3" GridColor="rgb(192, 192, 192)" GridHeight="10" '
    'GridVisible="false" GridWidth="10" Height="0" HideConnectorIfFromToIsHidden="0" HideEmptyTaggedValues="false" '
    'ImageHeight="0" ImageScale="1.0" ImageWidth="0" InitializeDiagramForCreate="true" Maximized="false" '
    'ModelElementNameAlignment="4" PaintConnectorThroughLabel="1" PointConnectorEndToCompartmentMember="true" '
    'QualityScore="-1" RequestFitSizeWithPromptUser="false" RequestValidateSnapToGrid="false" '
    'ShapePresentationOption="0" ShowActivityStateNodeCaption="524287" ShowAllocatedFrom="true" ShowAllocatedTo="true" '
    'ShowColorLegend="false" ShowConnectorLegend="false" ShowConnectorName="0" ShowConstraints="false" '
    'ShowDefaultPackage="false" ShowInformationItemOption="2" ShowPackageNameStyle="0" '
    'ShowRelationshipConstraintExpression="false" ShowSchemaName="true" ShowShapeLegend="false" '
    'ShowShapeStereotypeIconName="true" ShowStereotypes="true" ShowTaggedValues="false" TeamworkCreateDateTime="0" '
    'TrimmedHeight="0" TrimmedWidth="0" Width="0" X="0" Y="0" ZoomRatio="1.0" _alignColumnProperties="true" '
    '_columnConstraintsDisplayOption="2" _globalPaletteOption="true" _pointForeignKeyEndToColumn="false" '
    '_showColumnDefaultValue="false" _showColumnStereotypes="true" _showColumnTypes="true" _showColumnUserType="false" '
    '_showDBTableColumnsMode="1" _showDBViewColumnsMode="1" _showExtraColumnProperties="true"'
)
SHAPE_ATTRS = (
    f'Background="{FILL}" ColSortType="0" ConnectToPoint="true" ConnectionPointType="2" CoverConnector="false" '
    'CreatorDiagramType="ERDiagram" Foreground="rgb(0, 0, 0)" ModelElementNameAlignment="4" '
    'OverrideAppearanceWithStereotypeIcon="true" ParentConnectorDTheta="0.0" ParentConnectorHeaderLength="40" '
    'ParentConnectorLineLength="10" PresentationOption="4" PrimitiveShapeType="0" RequestDefaultSize="false" '
    'RequestFitSize="false" RequestFitSizeFromCenter="false" RequestResetCaption="false" '
    'RequestResetCaptionFitWidth="false" RequestResetCaptionSize="false" RequestSetSizeOption="0" Selectable="true" '
    'ShowAllocatedFrom="0" ShowAllocatedTo="0" ShowColumnStereotypes="0" ShowColumnsMode="0" '
    'ShowStereotypeIconName="0" ShowTaggedValues="0"'
)
CONNECTOR_ATTRS = (
    'Background="rgb(122, 207, 245)" ConnectorLabelOrientation="4" ConnectorLineJumps="4" '
    'ConnectorStyle="Follow Diagram" CreatorDiagramType="ERDiagram" Foreground="rgb(0, 0, 0)" FromConnectType="0" '
    'FromPinType="2" FromShapeXDiff="0" FromShapeYDiff="0" HasFromLabel="false" HasToLabel="false" '
    'ModelElementNameAlignment="4" Name="" PaintThroughLabel="2" RequestRebuild="false" RequestRebuildFromEnd="false" '
    'RequestRebuildToEnd="false" RequestResetCaption="false" RequestResetCaptionFitWidth="false" '
    'RequestResetCaptionSize="false" RequestResetLabels="false" Selectable="true" ShowConnectorName="2" '
    'ToConnectType="0" ToPinType="2" ToShapeXDiff="0" ToShapeYDiff="0" UseFromShapeCenter="true" UseToShapeCenter="true"'
)
FONT = '<ElementFont Color="rgb(0, 0, 0)" Name="Dialog" Size="11" Style="0"/>'
PAD = 50  # VP pads a line's box by 50 around its points; a tighter box clips the |— and o< end symbols
LINE = '<Line Cap="0" Color="rgb(0, 0, 0)" Transparency="0" Weight="1.0"><Stroke/></Line>'


def new_id() -> str:
    return "".join(random.choice(ID_CHARS) for _ in range(16))


def attrs(**values: object) -> str:
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in values.items())


def height(table: Table) -> int:
    return HEADER + ROW * len(table.cols)


def build_xml(schema: list[Table]) -> str:
    by_name = {t.name: t for t in schema}
    model_id = {t.name: new_id() for t in schema}
    shape_id = {t.name: new_id() for t in schema}
    col_id = {(t.name, c.name): new_id() for t in schema for c in t.cols}

    # one relationship per foreign-key column: parent (referenced table) → child (table holding the column)
    rels = []
    for t in schema:
        for c in t.cols:
            if c.fk:
                parent, ref_col = c.fk.split(".")
                if parent not in by_name or (parent, ref_col) not in col_id:
                    raise SystemExit(f"{t.name}.{c.name} references {c.fk}, which is not in SCHEMA")
                rels.append({"id": new_id(), "shape": new_id(), "parent": parent, "child": t.name,
                             "column": c.name, "ref": col_id[(parent, ref_col)], "optional": c.nullable})
    rel_for = {(r["child"], r["column"]): r for r in rels}

    def column(t: Table, c: Col) -> str:
        head = "<DBColumn " + attrs(
            Id=col_id[(t.name, c.name)], Name=c.name, Type=c.type, Type_internalValue=TYPE_CODE[c.type],
            Length=c.length, Nullable=str(c.nullable).lower(), PrimaryKey=str(c.pk).lower(), Unique=str(c.unique).lower(),
        )
        if not c.fk:
            return head + "/>"
        r = rel_for[(t.name, c.name)]
        return (f"{head}><ForeignKeyConstraints><DBForeignKeyConstraint "
                f'{attrs(Id=new_id(), Name="", ForeignKey=r["id"], RefColumn=r["ref"])}/></ForeignKeyConstraints></DBColumn>')

    def table_model(t: Table) -> str:
        frm = [r["id"] for r in rels if r["parent"] == t.name]
        to = [r["id"] for r in rels if r["child"] == t.name]
        links = ""
        if frm:
            links += "<FromSimpleRelationships>" + "".join(f'<DBForeignKey Idref="{i}" Name=""/>' for i in frm) + "</FromSimpleRelationships>"
        if to:
            links += "<ToSimpleRelationships>" + "".join(f'<DBForeignKey Idref="{i}" Name=""/>' for i in to) + "</ToSimpleRelationships>"
        return (f'<DBTable {attrs(Id=model_id[t.name], Name=t.name, DataModel="Physical")}><ModelChildren>'
                + "".join(column(t, c) for c in t.cols)
                + f'</ModelChildren>{links}<MasterView><DBTable {attrs(Idref=shape_id[t.name], Name=t.name)}/></MasterView></DBTable>')

    # Drawing order: tables first, lines after, so the tables never cover the ends of a line.
    z_order = {name: i for i, name in enumerate([t.name for t in schema] + [r["shape"] for r in rels], start=1)}

    def table_shape(t: Table) -> str:
        return (f'<DBTable {SHAPE_ATTRS} {attrs(Id=shape_id[t.name], Model=model_id[t.name], MetaModelElement=model_id[t.name], Name=t.name, X=t.x, Y=t.y, Width=t.width, Height=height(t), ZOrder=z_order[t.name])}>'
                f'{FONT}{LINE}<Caption Height="15" InternalHeight="-2147483648" InternalWidth="-2147483648" Side="FreeMove" Visible="true" Width="{t.width + 1}" X="0" Y="0"/>'
                f'<FillColor Color="{FILL}" Style="1" Transparency="0" Type="1"/><CompartmentFont Value="none"/></DBTable>')

    def connector(r: dict) -> str:
        """A straight line between the facing sides of the two tables, at a height both share."""
        p, c = by_name[r["parent"]], by_name[r["child"]]
        top, bottom = max(p.y, c.y), min(p.y + height(p), c.y + height(c))
        if top >= bottom:
            raise SystemExit(f"{p.name} and {c.name} share no height; place them side by side for a straight line")
        y = (top + bottom) // 2 if bottom - top < 120 else top + 56  # near the top, by the key rows
        same_pair = [x for x in rels if (x["parent"], x["child"]) == (r["parent"], r["child"])]
        y += same_pair.index(r) * 2 * ROW  # a second link between the same two tables runs below the first
        left_to_right = p.x < c.x
        x1 = p.x + p.width if left_to_right else p.x
        x2 = c.x if left_to_right else c.x + c.width
        pin = lambda t, side: f'X="{side}" Y="{(y - t.y) / height(t)}"'  # noqa: E731
        return (f'<DBForeignKey {CONNECTOR_ATTRS} {attrs(Id=r["shape"], Model=r["id"], MetaModelElement=r["id"], From=shape_id[p.name], To=shape_id[c.name], X=min(x1, x2) - PAD, Y=y - PAD, Width=abs(x2 - x1) + 2 * PAD, Height=2 * PAD, ZOrder=z_order[r["shape"]])}>'
                f'<FromPinRatio {pin(p, "1.0" if left_to_right else "0.0")}/><ToPinRatio {pin(c, "0.0" if left_to_right else "1.0")}/>'
                f'{FONT}{LINE}<Caption Height="0" InternalHeight="-2147483648" InternalWidth="-2147483648" Side="None" Visible="true" Width="20" X="{min(x1, x2)}" Y="{y}"/>'
                f'<Points><Point X="{x1}.0" Y="{y}.0"/><Point X="{x2}.0" Y="{y}.0"/></Points></DBForeignKey>')

    fks = "".join(
        f'<DBForeignKey {attrs(Id=r["id"], Name="", From=model_id[r["parent"]], To=model_id[r["child"]], FromMultiplicity="0..1" if r["optional"] else "1", ToMultiplicity="0..*", Identifying="false", DataModel="Physical")}>'
        f'<MasterView><DBForeignKey Idref="{r["shape"]}" Name=""/></MasterView></DBForeignKey>'
        for r in rels
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Project {attrs(Name=NAME, UmlVersion="2.x", Xml_structure="simple", ExporterVersion="16.1.1")}>
<Models>
{"".join(table_model(t) for t in schema)}
<ModelRelationshipContainer Id="{new_id()}" Name="relationships"><ModelChildren>
<ModelRelationshipContainer Id="{new_id()}" Name="DBForeignKey"><ModelChildren>{fks}</ModelChildren></ModelRelationshipContainer>
</ModelChildren></ModelRelationshipContainer>
</Models>
<Diagrams>
<ERDiagram {DIAGRAM_ATTRS} {attrs(Id=new_id(), Name=NAME)}>
<Shapes>{"".join(table_shape(t) for t in schema)}</Shapes>
<Connectors>{"".join(connector(r) for r in rels)}</Connectors>
</ERDiagram>
</Diagrams>
</Project>
'''


def visual_paradigm() -> tuple[Path, str]:
    """The Visual Paradigm install's bin folder and the classpath its own command-line scripts use."""
    installs = sorted(glob.glob(r"C:\Program Files\Visual Paradigm*\bin"))
    if not installs:
        raise SystemExit("Visual Paradigm is not installed under C:\\Program Files")
    jars = [r"..\lib\vpplatform.jar", r"..\lib\jniwrap.jar", r"..\lib\winpack.jar", r"..\ormlib\orm.jar",
            r"..\ormlib\orm-core.jar"] + [rf"..\lib\lib{i:02d}.jar" for i in range(1, 21)]
    return Path(installs[-1]), ";".join(["."] + jars)


def run_vp(tool: str, *args: str) -> None:
    bin_dir, classpath = visual_paradigm()
    java = bin_dir.parent / "jre" / "bin" / "java.exe"
    done = subprocess.run([str(java), "-Xms256m", "-Xmx768m", "-Djava.awt.headless=true", "-cp", classpath,
                           f"com.vp.cmd.{tool}", *args], cwd=bin_dir, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise SystemExit(f"Visual Paradigm {tool} failed:\n{done.stdout}\n{done.stderr}")


def white_background(image: Path) -> None:
    """Visual Paradigm exports PNG and SVG with a transparent background; put the diagram on white."""
    if image.suffix == ".svg":
        text = image.read_text(encoding="utf-8")
        start = text.index(">", text.index("<svg")) + 1
        image.write_text(text[:start] + '<rect width="100%" height="100%" fill="#ffffff"/>' + text[start:], encoding="utf-8")
    elif image.suffix == ".png":
        try:
            from PIL import Image
        except ImportError:
            sys.exit("The PNG background needs Pillow: python -m pip install pillow")
        with Image.open(image) as drawn:
            white = Image.new("RGBA", drawn.size, (255, 255, 255, 255))
            white.alpha_composite(drawn.convert("RGBA"))
        white.convert("RGB").save(image)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replace", action="store_true", help="rebuild an existing .vpp (loses edits made in the app)")
    parser.add_argument("--type", default="png", choices=["png", "jpg", "svg", "pdf"], help="image type (default png)")
    args = parser.parse_args()

    vpp, image = HERE / f"{NAME}.vpp", HERE / f"{NAME}.{args.type}"
    if vpp.exists() and not args.replace:
        sys.exit(f'"{vpp.name}" already exists. Rerun with --replace to rebuild it (edits made in the app are lost).')
    with tempfile.TemporaryDirectory(prefix="erd-") as tmp:  # VP leaves .bak files next to what it opens
        work = Path(tmp)
        (work / "erd.xml").write_text(build_xml(SCHEMA), encoding="utf-8")
        (work / "diagrams.txt").write_text(NAME + "\n", encoding="utf-8")
        run_vp("ImportXML", "-project", str(work / "erd.vpp"), "-file", str(work / "erd.xml"))
        run_vp("ExportDiagramImage", "-project", str(work / "erd.vpp"), "-out", str(work / f"erd.{args.type}"),
               "-diagram", f"@{work / 'diagrams.txt'}", "-type", args.type)
        if not (work / f"erd.{args.type}").is_file():
            sys.exit("Visual Paradigm wrote no image; check the diagram name in the project.")
        white_background(work / f"erd.{args.type}")
        shutil.copyfile(work / "erd.vpp", vpp)
        shutil.copyfile(work / f"erd.{args.type}", image)
    print(f"Wrote {vpp}\nWrote {image}")


if __name__ == "__main__":
    main()
