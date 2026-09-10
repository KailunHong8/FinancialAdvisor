"""Pydantic v2 models for the three config files, loaded and validated at startup (§6).

A bad threshold or an in-scope account missing its bank mapping fails here, not mid-run.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class Account(BaseModel):
    ledger_account: str
    label: str | None = None
    bank: str | None = None
    bank_account: str | None = None
    clabe: str | None = None
    pos_terminal: str | None = None
    currency: str = "MXN"
    in_scope: bool = True
    dormant: bool = False
    reason: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check_scope(self):
        if self.in_scope and (not self.bank or not self.bank_account):
            raise ValueError(
                f"in-scope account {self.ledger_account} needs both bank and bank_account")
        return self


class EntityConfig(BaseModel):
    entity: str
    currency: str = "MXN"
    materiality: Decimal
    ledger_root: str
    ledger_rollup_accounts: list[str] = Field(default_factory=list)
    accounts: list[Account]
    charge_account_map: dict[str, str] = Field(default_factory=dict)

    def by_ledger_account(self, acct: str) -> Account | None:
        for a in self.accounts:
            if a.ledger_account == acct:
                return a
        return None

    def by_bank_account(self, bank_account: str) -> Account | None:
        for a in self.accounts:
            if a.bank_account == bank_account:
                return a
        return None

    @property
    def in_scope_accounts(self) -> list[Account]:
        return [a for a in self.accounts if a.in_scope]


class SubsetSum(BaseModel):
    max_pool: int = 24
    max_subset_size: int = 4
    require_unique_solution: bool = True


class MatchDefaults(BaseModel):
    date_window_days: int = 3
    amount_tolerance: Decimal = Decimal("0.00")
    description_min_similarity: float = 0.55
    min_score_margin: float = 0.15
    subset_sum: SubsetSum = Field(default_factory=SubsetSum)


class MatchingConfig(BaseModel):
    defaults: MatchDefaults = Field(default_factory=MatchDefaults)
    accounts: dict[str, dict] = Field(default_factory=dict)

    def for_account(self, ledger_account: str) -> MatchDefaults:
        """Per-account values override defaults key-by-key (§6.2)."""
        base = self.defaults.model_dump()
        override = self.accounts.get(ledger_account, {})
        for k, v in override.items():
            base[k] = v
        return MatchDefaults.model_validate(base)


class BankConfig(BaseModel):
    bank_name: str
    parser_version: str
    sniff_any: list[str]
    header_fields: dict[str, str]
    table: dict
    wrapped_description: bool = True
    reference_regex: str | None = None
    known_codes: dict[str, dict] = Field(default_factory=dict)
    description_categories: list[dict] = Field(default_factory=list)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Config:
    """Bundle of the three config files plus their combined hash (for the runs table)."""

    def __init__(self, config_dir: str | Path):
        self.dir = Path(config_dir)
        self.entity = EntityConfig.model_validate(
            yaml.safe_load((self.dir / "entities" / "secontrol.yml").read_text()))
        self.matching = MatchingConfig.model_validate(
            yaml.safe_load((self.dir / "matching.yml").read_text()))
        self._banks: dict[str, BankConfig] = {}
        self.config_sha256 = self._combined_hash()

    def bank(self, name: str) -> BankConfig:
        if name not in self._banks:
            path = self.dir / "banks" / f"{name}.yml"
            self._banks[name] = BankConfig.model_validate(yaml.safe_load(path.read_text()))
        return self._banks[name]

    def bank_template_sha256(self, name: str) -> str:
        return _sha256_file(self.dir / "banks" / f"{name}.yml")

    def _combined_hash(self) -> str:
        h = hashlib.sha256()
        for p in sorted(self.dir.rglob("*.yml")):
            h.update(p.read_bytes())
        return h.hexdigest()
