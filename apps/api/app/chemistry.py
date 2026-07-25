from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

try:
    from rdkit import Chem, rdBase
    from rdkit.Chem import Descriptors, rdMolDescriptors
except ImportError:  # Allows the non-chemistry modules to be reviewed without RDKit.
    Chem = None
    rdBase = None
    Descriptors = None
    rdMolDescriptors = None


@dataclass(frozen=True)
class StructureRecord:
    standardized_smiles: str
    inchi: str | None
    inchi_key: str | None
    molecular_formula: str | None
    molecular_weight: float | None
    structure_hash: str
    toolkit_name: str
    toolkit_version: str
    computed_at: str

    def as_dict(self) -> dict:
        return asdict(self)


def standardize_smiles(smiles: str) -> StructureRecord:
    value = smiles.strip()
    if not value:
        raise ValueError("SMILES is empty")
    if Chem is None:
        raise RuntimeError("RDKit is required to standardize structures")
    mol = Chem.MolFromSmiles(value)
    if mol is None:
        raise ValueError("SMILES could not be parsed by RDKit")
    Chem.SanitizeMol(mol)
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    inchi = Chem.MolToInchi(mol)
    inchi_key = Chem.InchiToInchiKey(inchi)
    return StructureRecord(
        standardized_smiles=canonical,
        inchi=inchi,
        inchi_key=inchi_key,
        molecular_formula=rdMolDescriptors.CalcMolFormula(mol),
        molecular_weight=round(float(Descriptors.MolWt(mol)), 6),
        structure_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        toolkit_name="RDKit",
        toolkit_version=rdBase.rdkitVersion,
        computed_at=datetime.now(UTC).isoformat(),
    )


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


FUNCTIONAL_GROUPS = (
    ("fg:amide:v1", "amide", "[NX3][CX3](=[OX1])", "mvp-smarts-v1"),
    ("fg:carboxylic_acid:v1", "carboxylic acid", "[CX3](=O)[OX2H1]", "mvp-smarts-v1"),
    ("fg:acyl_halide:v1", "acyl halide", "[CX3](=O)[F,Cl,Br,I]", "mvp-smarts-v1"),
    ("fg:alcohol:v1", "alcohol", "[OX2H][CX4]", "mvp-smarts-v1"),
    ("fg:amine:v1", "amine", "[NX3;H2,H1;!$(NC=O)]", "mvp-smarts-v1"),
)


def annotate_compound(connection, compound_id: str, smiles: str) -> StructureRecord:
    """Persist deterministic structure, element and versioned SMARTS annotations."""
    record = standardize_smiles(smiles)
    connection.execute(
        """INSERT INTO compound_property
        (compound_id, standardized_smiles, molecular_formula, molecular_weight,
         structure_hash, toolkit_name, toolkit_version, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(compound_id) DO UPDATE SET
          standardized_smiles=excluded.standardized_smiles,
          molecular_formula=excluded.molecular_formula,
          molecular_weight=excluded.molecular_weight,
          structure_hash=excluded.structure_hash,
          toolkit_name=excluded.toolkit_name,
          toolkit_version=excluded.toolkit_version,
          computed_at=excluded.computed_at""",
        (compound_id, record.standardized_smiles, record.molecular_formula,
         record.molecular_weight, record.structure_hash, record.toolkit_name,
         record.toolkit_version, record.computed_at),
    )
    mol = Chem.MolFromSmiles(record.standardized_smiles)
    element_counts: dict[int, int] = {}
    for atom in mol.GetAtoms():
        element_counts[atom.GetAtomicNum()] = element_counts.get(atom.GetAtomicNum(), 0) + 1
    periodic = Chem.GetPeriodicTable()
    connection.execute("DELETE FROM compound_element WHERE compound_id = ?", (compound_id,))
    for atomic_number, count in sorted(element_counts.items()):
        symbol = periodic.GetElementSymbol(atomic_number)
        connection.execute(
            """INSERT OR IGNORE INTO element (element_id, atomic_number, symbol, name)
               VALUES (?, ?, ?, ?)""",
            (atomic_number, atomic_number, symbol, periodic.GetElementName(atomic_number)),
        )
        connection.execute(
            "INSERT INTO compound_element (compound_id, element_id, atom_count) VALUES (?, ?, ?)",
            (compound_id, atomic_number, count),
        )
    connection.execute("DELETE FROM compound_functional_group WHERE compound_id = ?", (compound_id,))
    for group_id, name, smarts, version in FUNCTIONAL_GROUPS:
        connection.execute(
            """INSERT OR IGNORE INTO functional_group
               (functional_group_id, preferred_name, smarts, detector_version)
               VALUES (?, ?, ?, ?)""",
            (group_id, name, smarts, version),
        )
        pattern = Chem.MolFromSmarts(smarts)
        match_count = len(mol.GetSubstructMatches(pattern))
        if match_count:
            connection.execute(
                """INSERT INTO compound_functional_group
                   (compound_id, functional_group_id, match_count, detector_version)
                   VALUES (?, ?, ?, ?)""",
                (compound_id, group_id, match_count, version),
            )
    return record
