"""
Neo4j client for knowledge graph operations.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional, Union

from neo4j import GraphDatabase, Driver, Session
from neo4j.exceptions import ServiceUnavailable
from loguru import logger

from src.config import get_settings


class Neo4jClient:
    """Client for interacting with Neo4j knowledge graph."""

    def __init__(self, uri: Optional[str] = None, user: Optional[str] = None, password: Optional[str] = None):
        """
        Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI
            user: Database user
            password: Database password
        """
        settings = get_settings()
        self.uri = uri or settings.neo4j_uri
        self.user = user or settings.neo4j_user
        self.password = password or settings.neo4j_password
        self.database = settings.neo4j_database

        self._driver: Optional[Driver] = None

    def connect(self) -> None:
        """Establish connection to Neo4j."""
        try:
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password)
            )
            # Test connection
            self._driver.verify_connectivity()
            logger.info(f"Connected to Neo4j at {self.uri}")
        except ServiceUnavailable as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            raise

    def close(self) -> None:
        """Close connection to Neo4j."""
        if self._driver:
            self._driver.close()
            logger.info("Neo4j connection closed")

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    @property
    def session(self) -> Session:
        """Get a new session."""
        if not self._driver:
            raise RuntimeError("Not connected to Neo4j. Call connect() first.")
        return self._driver.session(database=self.database)

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a Cypher query and return results.

        Args:
            query: Cypher query string
            parameters: Query parameters

        Returns:
            List of result records as dictionaries
        """
        with self.session as session:
            result = session.run(query, parameters or {})
            return [dict(record) for record in result]

    def execute_write(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Execute a write query.

        Args:
            query: Cypher query string
            parameters: Query parameters
        """
        with self.session as session:
            session.execute_write(lambda tx: tx.run(query, parameters or {}))

    def create_constraints(self) -> None:
        """Create necessary database constraints and indexes."""
        constraints = [
            # Unique constraints
            "CREATE CONSTRAINT part_id IF NOT EXISTS FOR (p:Part) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT supplier_id IF NOT EXISTS FOR (s:Supplier) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT bom_id IF NOT EXISTS FOR (b:BOM) REQUIRE b.id IS UNIQUE",
            "CREATE CONSTRAINT po_id IF NOT EXISTS FOR (po:PurchaseOrder) REQUIRE po.id IS UNIQUE",

            # Indexes for performance
            "CREATE INDEX part_category IF NOT EXISTS FOR (p:Part) ON (p.category)",
            "CREATE INDEX part_criticality IF NOT EXISTS FOR (p:Part) ON (p.criticality)",
            "CREATE INDEX supplier_status IF NOT EXISTS FOR (s:Supplier) ON (s.status)",
            "CREATE INDEX bom_status IF NOT EXISTS FOR (b:BOM) ON (b.status)",
        ]

        for constraint in constraints:
            try:
                self.execute_write(constraint)
                logger.info(f"Created constraint/index: {constraint[:50]}...")
            except Exception as e:
                logger.warning(f"Constraint/index may already exist: {e}")

    def create_part(
        self,
        part_id: str,
        name: str,
        description: str,
        category: str,
        criticality: str,
        specifications: Dict[str, Any],
        **kwargs
    ) -> None:
        """
        Create a Part node.

        Args:
            part_id: Unique part identifier
            name: Part name
            description: Part description
            category: Part category
            criticality: Criticality level
            specifications: Technical specifications
            **kwargs: Additional properties
        """
        # Convert specifications dict to JSON string
        specs_json = json.dumps(specifications)
        unit_of_measure = kwargs.pop("unit_of_measure", "EA")

        query = """
        MERGE (p:Part {id: $id})
        ON CREATE SET
            p.name               = $name,
            p.description        = $description,
            p.category           = $category,
            p.criticality        = $criticality,
            p.specifications_json = $specifications_json,
            p.unit_of_measure    = $unit_of_measure,
            p.created_at         = datetime()
        ON MATCH SET
            p.name               = $name,
            p.description        = $description,
            p.category           = $category,
            p.criticality        = $criticality,
            p.specifications_json = $specifications_json,
            p.unit_of_measure    = $unit_of_measure
        """

        parameters = {
            "id": part_id,
            "name": name,
            "description": description,
            "category": category,
            "criticality": criticality,
            "specifications_json": specs_json,
            "unit_of_measure": unit_of_measure,
            **kwargs
        }

        self.execute_write(query, parameters)
        logger.info(f"Created Part: {part_id}")
        # Generate embedding for similarity search (non-blocking — failure won't break create)
        try:
            from src.search.embedder import embed, part_text
            from src.search.vector_store import upsert as vec_upsert
            text = part_text({"name": name, "description": description,
                              "category": category, "criticality": criticality,
                              "specifications_json": specs_json})
            vec_upsert(part_id, "part", name, embed(text))
        except Exception as _e:
            logger.debug(f"Part embedding skipped: {_e}")

    def create_supplier(
        self,
        supplier_id: str,
        name: str,
        location: str,
        certifications: List[str],
        status: str = "ACTIVE",
        **kwargs
    ) -> None:
        """
        Create a Supplier node.

        Args:
            supplier_id: Unique supplier identifier
            name: Supplier name
            location: Supplier location
            certifications: List of certifications
            status: Supplier status
            **kwargs: Additional properties
        """
        # Convert contact_info dict to JSON if present
        contact_info = kwargs.pop('contact_info', {})
        contact_info_json = json.dumps(contact_info) if contact_info else '{}'
        tier = kwargs.pop('tier', 2)
        rating = kwargs.pop('rating', 0.0)
        established_date = kwargs.pop('established_date', None)

        query = """
        MERGE (s:Supplier {id: $id})
        ON CREATE SET
            s.name              = $name,
            s.location          = $location,
            s.certifications    = $certifications,
            s.status            = $status,
            s.contact_info_json = $contact_info_json,
            s.tier              = $tier,
            s.rating            = $rating,
            s.established_date  = $established_date,
            s.created_at        = datetime()
        ON MATCH SET
            s.name              = $name,
            s.location          = $location,
            s.certifications    = $certifications,
            s.status            = $status,
            s.contact_info_json = $contact_info_json,
            s.tier              = $tier,
            s.rating            = $rating,
            s.established_date  = $established_date
        """

        parameters = {
            "id": supplier_id,
            "name": name,
            "location": location,
            "certifications": certifications,
            "status": status,
            "contact_info_json": contact_info_json,
            "tier": tier,
            "rating": rating,
            "established_date": established_date,
            **kwargs
        }

        self.execute_write(query, parameters)
        logger.info(f"Created Supplier: {supplier_id}")
        try:
            from src.search.embedder import embed, supplier_text
            from src.search.vector_store import upsert as vec_upsert
            text = supplier_text({"name": name, "location": location,
                                  "certifications": certifications})
            vec_upsert(supplier_id, "supplier", name, embed(text))
        except Exception as _e:
            logger.debug(f"Supplier embedding skipped: {_e}")

    def create_supplies_relationship(
        self,
        supplier_id: str,
        part_id: str,
        valid_from: Union[str, date],
        lead_time_days: int,
        price: float,
        currency: str = "USD",
        valid_to: Optional[Union[str, date]] = None,
        **kwargs
    ) -> None:
        """
        Create SUPPLIES relationship between Supplier and Part.

        Args:
            supplier_id: Supplier ID
            part_id: Part ID
            valid_from: Relationship start date
            lead_time_days: Lead time in days
            price: Unit price
            currency: Price currency
            valid_to: Relationship end date (None if current)
            **kwargs: Additional properties
        """
        # Extract optional fields from kwargs with defaults
        min_order_quantity = kwargs.pop("min_order_quantity", None)
        on_time_delivery_rate = kwargs.pop("on_time_delivery_rate", None)
        quality_rating = kwargs.pop("quality_rating", None)
        source = kwargs.pop("source", "manual_entry")
        confidence = kwargs.pop("confidence", 1.0)

        query = """
        MATCH (s:Supplier {id: $supplier_id})
        MATCH (p:Part {id: $part_id})
        MERGE (s)-[r:SUPPLIES {valid_from: date($valid_from)}]->(p)
        SET r.valid_to                = CASE WHEN $valid_to IS NULL THEN NULL ELSE date($valid_to) END,
            r.lead_time_days          = $lead_time_days,
            r.price                   = $price,
            r.currency                = $currency,
            r.min_order_quantity      = $min_order_quantity,
            r.on_time_delivery_rate   = $on_time_delivery_rate,
            r.quality_rating          = $quality_rating,
            r.source                  = $source,
            r.confidence              = $confidence,
            r.created_at              = COALESCE(r.created_at, datetime())
        """

        parameters = {
            "supplier_id":           supplier_id,
            "part_id":               part_id,
            "valid_from":            str(valid_from) if isinstance(valid_from, date) else valid_from,
            "valid_to":              str(valid_to) if isinstance(valid_to, date) else valid_to,
            "lead_time_days":        lead_time_days,
            "price":                 price,
            "currency":              currency,
            "min_order_quantity":    min_order_quantity,
            "on_time_delivery_rate": on_time_delivery_rate,
            "quality_rating":        quality_rating,
            "source":                source,
            "confidence":            confidence,
        }

        self.execute_write(query, parameters)
        logger.info(f"Created SUPPLIES: {supplier_id} -> {part_id}")

    def query_current_suppliers(self, part_id: str) -> List[Dict[str, Any]]:
        """
        Get all current suppliers for a part.

        Args:
            part_id: Part identifier

        Returns:
            List of supplier information with relationship details
        """
        query = """
        MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part {id: $part_id})
        WHERE r.valid_to IS NULL
          AND s.status = 'ACTIVE'
        RETURN s.id as supplier_id,
               s.name as supplier_name,
               s.location as location,
               r.lead_time_days as lead_time_days,
               r.price as price,
               r.currency as currency,
               r.on_time_delivery_rate as on_time_delivery_rate
        ORDER BY r.lead_time_days
        """

        return self.execute_query(query, {"part_id": part_id})

    def query_suppliers_at_date(
        self,
        part_id: str,
        as_of_date: Union[str, date]
    ) -> List[Dict[str, Any]]:
        """
        Get suppliers for a part as of a specific date (temporal query).

        Args:
            part_id: Part identifier
            as_of_date: Date for historical query

        Returns:
            List of supplier information as of that date
        """
        query = """
        MATCH (s:Supplier)-[r:SUPPLIES]->(p:Part {id: $part_id})
        WHERE r.valid_from <= date($as_of_date)
          AND (r.valid_to IS NULL OR r.valid_to > date($as_of_date))
        RETURN s.id as supplier_id,
               s.name as supplier_name,
               r.lead_time_days as lead_time_days,
               r.price as price,
               r.valid_from as valid_from,
               r.valid_to as valid_to
        ORDER BY r.price
        """

        date_str = str(as_of_date) if isinstance(as_of_date, date) else as_of_date
        return self.execute_query(query, {"part_id": part_id, "as_of_date": date_str})

    def assess_supplier_disruption(self, supplier_id: str) -> Dict[str, Any]:
        """
        Assess impact of supplier disruption.

        Args:
            supplier_id: Supplier identifier

        Returns:
            Dictionary with affected parts and criticality analysis
        """
        query = """
        MATCH (s:Supplier {id: $supplier_id})-[r:SUPPLIES]->(p:Part)
        WHERE r.valid_to IS NULL
        RETURN p.id as part_id,
               p.name as part_name,
               p.criticality as criticality,
               COUNT{(b:BOM)-[:CONTAINS]->(:Component)-[:REFERENCES]->(p)} as bom_count
        ORDER BY p.criticality DESC, bom_count DESC
        """

        affected_parts = self.execute_query(query, {"supplier_id": supplier_id})

        return {
            "supplier_id": supplier_id,
            "affected_parts_count": len(affected_parts),
            "affected_parts": affected_parts,
            "critical_parts": [p for p in affected_parts if p["criticality"] in ["HIGH", "CRITICAL"]]
        }

    # ─── BOM ──────────────────────────────────────────────────────────────────────

    def create_bom(
        self,
        bom_id: str,
        name: str,
        description: str,
        version: str,
        status: str = "DRAFT",
        **kwargs
    ) -> None:
        """Create a BOM node."""
        query = """
        CREATE (b:BOM {
            id: $id,
            name: $name,
            description: $description,
            version: $version,
            status: $status,
            created_at: datetime()
        })
        """
        self.execute_write(query, {
            "id": bom_id, "name": name, "description": description,
            "version": version, "status": status,
        })
        logger.info(f"Created BOM: {bom_id}")

    def add_bom_component(
        self,
        bom_id: str,
        part_id: str,
        quantity: float,
        reference_designator: str = "",
        unit_of_measure: str = "EA",
        notes: str = "",
    ) -> None:
        """
        Add a Part to a BOM via an intermediate Component node.

        Graph pattern:  (BOM)-[:CONTAINS]->(Component)-[:REFERENCES]->(Part)

        The Component node carries quantity and assembly metadata;
        keeping it separate from the relationship allows multiple
        BOMs to reference the same Part with different quantities/notes.
        """
        component_id = f"COMP-{bom_id}-{part_id}"
        query = """
        MATCH (b:BOM {id: $bom_id})
        MATCH (p:Part {id: $part_id})
        MERGE (c:Component {id: $component_id})
          ON CREATE SET
            c.quantity           = $quantity,
            c.reference_designator = $reference_designator,
            c.unit_of_measure    = $unit_of_measure,
            c.notes              = $notes,
            c.created_at         = datetime()
        MERGE (b)-[:CONTAINS]->(c)
        MERGE (c)-[:REFERENCES]->(p)
        """
        self.execute_write(query, {
            "bom_id": bom_id, "part_id": part_id,
            "component_id": component_id,
            "quantity": quantity,
            "reference_designator": reference_designator,
            "unit_of_measure": unit_of_measure,
            "notes": notes,
        })
        logger.info(f"Added component {part_id} (qty {quantity}) to BOM {bom_id}")

    def get_bom(self, bom_id: str) -> dict | None:
        """Return BOM node properties or None if not found."""
        rows = self.execute_query(
            "MATCH (b:BOM {id: $id}) RETURN b.id AS id, b.name AS name, "
            "b.description AS description, b.version AS version, b.status AS status, "
            "toString(b.created_at) AS created_at",
            {"id": bom_id}
        )
        return rows[0] if rows else None

    def list_boms(self, status: str | None = None) -> list:
        """Return all BOMs with component count, optionally filtered by status."""
        where = "WHERE b.status = $status" if status else ""
        query = f"""
        MATCH (b:BOM)
        {where}
        OPTIONAL MATCH (b)-[:CONTAINS]->(c:Component)
        RETURN b.id AS id, b.name AS name, b.version AS version,
               b.status AS status, b.description AS description,
               count(c) AS component_count
        ORDER BY b.id
        """
        params = {"status": status} if status else {}
        return self.execute_query(query, params)

    def get_bom_components(self, bom_id: str) -> list:
        """Return all components in a BOM with their part details."""
        query = """
        MATCH (b:BOM {id: $bom_id})-[:CONTAINS]->(c:Component)-[:REFERENCES]->(p:Part)
        RETURN c.id                    AS component_id,
               c.quantity              AS quantity,
               c.reference_designator  AS reference_designator,
               c.unit_of_measure       AS unit_of_measure,
               c.notes                 AS notes,
               p.id                    AS part_id,
               p.name                  AS part_name,
               p.category              AS category,
               p.criticality           AS criticality
        ORDER BY c.reference_designator, p.id
        """
        return self.execute_query(query, {"bom_id": bom_id})

    def get_bom_risk_assessment(self, bom_id: str) -> dict:
        """
        Assess supply risk for a BOM.

        For each component part, find how many active suppliers exist.
        Parts with zero or one supplier are flagged as at-risk.
        """
        query = """
        MATCH (b:BOM {id: $bom_id})-[:CONTAINS]->(c:Component)-[:REFERENCES]->(p:Part)
        OPTIONAL MATCH (s:Supplier)-[r:SUPPLIES]->(p)
        WHERE r.valid_to IS NULL AND s.status = 'ACTIVE'
        WITH p, c, count(s) AS supplier_count
        RETURN p.id          AS part_id,
               p.name        AS part_name,
               p.criticality AS criticality,
               c.quantity    AS quantity,
               supplier_count,
               CASE
                 WHEN supplier_count = 0 THEN 'NO_SUPPLIER'
                 WHEN supplier_count = 1 THEN 'SINGLE_SOURCE'
                 ELSE 'MULTI_SOURCE'
               END AS risk_level
        ORDER BY
          CASE p.criticality
            WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
            WHEN 'MEDIUM' THEN 2 ELSE 3
          END,
          supplier_count
        """
        components = self.execute_query(query, {"bom_id": bom_id})
        at_risk = [c for c in components if c["risk_level"] in ("NO_SUPPLIER", "SINGLE_SOURCE")]
        return {
            "bom_id": bom_id,
            "total_components": len(components),
            "at_risk_count": len(at_risk),
            "components": components,
            "at_risk_components": at_risk,
        }

    def get_boms_affected_by_part(self, part_id: str) -> list:
        """Return all BOMs that contain a given part."""
        query = """
        MATCH (b:BOM)-[:CONTAINS]->(c:Component)-[:REFERENCES]->(p:Part {id: $part_id})
        RETURN b.id AS bom_id, b.name AS bom_name, b.version AS version,
               b.status AS status, c.quantity AS quantity
        ORDER BY b.id
        """
        return self.execute_query(query, {"part_id": part_id})

    def delete_bom(self, bom_id: str) -> bool:
        """Delete a BOM and its Component nodes (Parts are preserved)."""
        rows = self.execute_query(
            "MATCH (b:BOM {id: $id}) RETURN b.id", {"id": bom_id}
        )
        if not rows:
            return False
        self.execute_write(
            "MATCH (b:BOM {id: $id})-[:CONTAINS]->(c:Component) DETACH DELETE b, c",
            {"id": bom_id}
        )
        # Also delete the BOM itself if it had no components
        self.execute_write(
            "MATCH (b:BOM {id: $id}) DETACH DELETE b", {"id": bom_id}
        )
        logger.info(f"Deleted BOM: {bom_id}")
        return True

    def clear_all_data(self) -> None:
        """Clear all nodes and relationships (USE WITH CAUTION)."""
        query = "MATCH (n) DETACH DELETE n"
        self.execute_write(query)
        logger.warning("Cleared all data from graph")
