"""
Load sample data into Neo4j knowledge graph.
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.graph.neo4j_client import Neo4jClient
from loguru import logger


def load_parts(client: Neo4jClient, filepath: Path):
    """Load parts from JSON file."""
    logger.info(f"Loading parts from {filepath}")
    
    with open(filepath) as f:
        parts = json.load(f)
    
    for part in parts:
        client.create_part(
            part_id=part["id"],
            name=part["name"],
            description=part["description"],
            category=part["category"],
            criticality=part["criticality"],
            specifications=part["specifications"],
            unit_of_measure=part.get("unit_of_measure", "EA")
        )
    
    logger.success(f"Loaded {len(parts)} parts")


def load_suppliers(client: Neo4jClient, filepath: Path):
    """Load suppliers from JSON file."""
    logger.info(f"Loading suppliers from {filepath}")
    
    with open(filepath) as f:
        suppliers = json.load(f)
    
    for supplier in suppliers:
        client.create_supplier(
            supplier_id=supplier["id"],
            name=supplier["name"],
            location=supplier["location"],
            certifications=supplier["certifications"],
            status=supplier.get("status", "ACTIVE"),
            contact_info=supplier.get("contact_info", {}),
            tier=supplier.get("tier", 2),
            rating=supplier.get("rating", 0.0),
            established_date=supplier.get("established_date")
        )
    
    logger.success(f"Loaded {len(suppliers)} suppliers")


def load_supply_relationships(client: Neo4jClient, filepath: Path):
    """Load SUPPLIES relationships from JSON file."""
    logger.info(f"Loading supply relationships from {filepath}")
    
    with open(filepath) as f:
        relationships = json.load(f)
    
    for rel in relationships:
        client.create_supplies_relationship(
            supplier_id=rel["supplier_id"],
            part_id=rel["part_id"],
            valid_from=rel["valid_from"],
            valid_to=rel.get("valid_to"),
            lead_time_days=rel["lead_time_days"],
            price=rel["price"],
            currency=rel.get("currency", "USD"),
            min_order_quantity=rel.get("min_order_quantity"),
            on_time_delivery_rate=rel.get("on_time_delivery_rate"),
            quality_rating=rel.get("quality_rating"),
            source=rel.get("source", "manual_entry"),
            confidence=rel.get("confidence", 1.0)
        )
    
    logger.success(f"Loaded {len(relationships)} supply relationships")


def load_compatibility(client: Neo4jClient, filepath: Path):
    """Load part compatibility relationships from JSON file."""
    logger.info(f"Loading compatibility relationships from {filepath}")
    
    with open(filepath) as f:
        compatibilities = json.load(f)
    
    for compat in compatibilities:
        # Convert constraints dict to JSON string
        constraints_json = json.dumps(compat.get("constraints", {}))
        
        query = """
        MATCH (original:Part {id: $original_id})
        MATCH (substitute:Part {id: $substitute_id})
        CREATE (original)-[r:COMPATIBLE_WITH {
            compatibility_type: $compatibility_type,
            validation_status: $validation_status,
            validated_by: $validated_by,
            validated_date: date($validated_date),
            constraints_json: $constraints_json,
            notes: $notes,
            created_at: datetime()
        }]->(substitute)
        """
        
        client.execute_write(query, {
            "original_id": compat["original_part_id"],
            "substitute_id": compat["substitute_part_id"],
            "compatibility_type": compat["compatibility_type"],
            "validation_status": compat["validation_status"],
            "validated_by": compat["validated_by"],
            "validated_date": compat["validated_date"],
            "constraints_json": constraints_json,
            "notes": compat.get("notes", "")
        })
    
    logger.success(f"Loaded {len(compatibilities)} compatibility relationships")


def verify_data(client: Neo4jClient):
    """Verify loaded data."""
    logger.info("Verifying loaded data...")
    
    # Count nodes
    counts_query = """
    MATCH (p:Part) WITH count(p) as part_count
    MATCH (s:Supplier) WITH part_count, count(s) as supplier_count
    MATCH ()-[r:SUPPLIES]->() WITH part_count, supplier_count, count(r) as supplies_count
    MATCH ()-[c:COMPATIBLE_WITH]->() 
    RETURN part_count, supplier_count, supplies_count, count(c) as compat_count
    """
    
    result = client.execute_query(counts_query)
    if result:
        counts = result[0]
        logger.info(f"Parts: {counts['part_count']}")
        logger.info(f"Suppliers: {counts['supplier_count']}")
        logger.info(f"Supply relationships: {counts['supplies_count']}")
        logger.info(f"Compatibility relationships: {counts['compat_count']}")
    
    # Test a query
    logger.info("\nTesting query: Current suppliers for P-12345")
    suppliers = client.query_current_suppliers("P-12345")
    for supplier in suppliers:
        logger.info(f"  - {supplier['supplier_name']}: "
                   f"${supplier['price']} @ {supplier['lead_time_days']} days")


def main():
    """Main function to load all sample data."""
    # Data directory
    data_dir = Path("data/sample")
    
    if not data_dir.exists():
        logger.error(f"Data directory not found: {data_dir}")
        logger.info("Run 'python scripts/generate_sample_data.py' first")
        return
    
    logger.info("Starting data load process...")
    logger.info("=" * 60)
    
    # Connect to Neo4j
    with Neo4jClient() as client:
        # Create constraints
        logger.info("Creating constraints and indexes...")
        client.create_constraints()
        
        # Clear existing data (optional - comment out to preserve data)
        logger.warning("Clearing existing data...")
        #client.clear_all_data()
        
        # Load data in order
        load_parts(client, data_dir / "parts.json")
        load_suppliers(client, data_dir / "suppliers.json")
        load_supply_relationships(client, data_dir / "supply_relationships.json")
        load_compatibility(client, data_dir / "compatibility.json")
        
        # Verify
        logger.info("\n" + "=" * 60)
        verify_data(client)
        
    logger.success("\n✓ Data load complete!")
    logger.info("Access Neo4j Browser at: http://localhost:7474")
    logger.info("Try this query: MATCH (n) RETURN n LIMIT 25")


if __name__ == "__main__":
    main()
