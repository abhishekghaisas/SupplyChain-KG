"""
Demonstration of the reasoning engine with supply chain rules.

This shows the SYMBOLIC component of the neuro-symbolic architecture.
"""

import sys
from pathlib import Path
from datetime import date, timedelta

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reasoning import (
    RulesEngine,
    PartCompatibilityRule,
    LeadTimeFeasibilityRule,
    SupplierQualificationRule,
    PriceReasonablenessRule,
    ProvenanceTracker
)
from src.graph.neo4j_client import Neo4jClient
from loguru import logger


def demo_part_compatibility():
    """Demonstrate part compatibility checking."""
    print("\n" + "="*70)
    print("DEMO 1: Part Compatibility Checking")
    print("="*70)
    
    # Get parts from Neo4j
    with Neo4jClient() as client:
        query = "MATCH (p:Part) WHERE p.id IN ['P-12345', 'P-67890'] RETURN p"
        results = client.execute_query(query)
        
        if len(results) < 2:
            print("⚠️  Parts not found in database. Run load_sample_data.py first.")
            return
        
        original = results[0]['p']
        substitute = results[1]['p']
    
    print(f"\n📦 Original Part: {original['id']} - {original['name']}")
    print(f"📦 Substitute Part: {substitute['id']} - {substitute['name']}")
    
    # Create rule and check
    rule = PartCompatibilityRule()
    result = rule.check(original, substitute)
    
    print(f"\n{result}")
    print(f"Confidence: {result.confidence:.2%}")
    print(f"Facts used: {', '.join(result.facts_used)}")
    
    if result.details:
        print("\nDetails:")
        for key, value in result.details.items():
            print(f"  - {key}: {value}")


def demo_lead_time_feasibility():
    """Demonstrate lead time feasibility checking."""
    print("\n" + "="*70)
    print("DEMO 2: Lead Time Feasibility")
    print("="*70)
    
    rule = LeadTimeFeasibilityRule()
    
    # Scenario 1: Feasible
    print("\n📅 Scenario 1: Feasible delivery")
    print("  - Supplier lead time: 21 days")
    print("  - Required date: 30 days from now")
    
    required_date = date.today() + timedelta(days=30)
    result1 = rule.check(
        supplier_lead_time_days=21,
        required_date=required_date
    )
    print(f"\n{result1}")
    print(f"Buffer: {result1.details.get('buffer_days')} days")
    
    # Scenario 2: Not feasible
    print("\n📅 Scenario 2: Not feasible")
    print("  - Supplier lead time: 35 days")
    print("  - Required date: 20 days from now")
    
    required_date = date.today() + timedelta(days=20)
    result2 = rule.check(
        supplier_lead_time_days=35,
        required_date=required_date
    )
    print(f"\n{result2}")
    if not result2.passed:
        print(f"Days late: {result2.details.get('days_late')}")


def demo_supplier_qualification():
    """Demonstrate supplier qualification checking."""
    print("\n" + "="*70)
    print("DEMO 3: Supplier Qualification")
    print("="*70)
    
    # Get supplier from Neo4j
    with Neo4jClient() as client:
        query = "MATCH (s:Supplier {id: 'SUP-001'}) RETURN s"
        results = client.execute_query(query)
        
        if not results:
            print("⚠️  Supplier not found in database.")
            return
        
        supplier = results[0]['s']
    
    print(f"\n🏢 Supplier: {supplier['name']}")
    print(f"   Location: {supplier['location']}")
    print(f"   Rating: {supplier['rating']}")
    print(f"   Certifications: {', '.join(supplier['certifications'])}")
    
    rule = SupplierQualificationRule()
    
    # Check with requirements
    result = rule.check(
        supplier=supplier,
        required_certifications=['ISO9001'],
        min_rating=4.0
    )
    
    print(f"\n{result}")
    print(f"Confidence: {result.confidence:.2%}")


def demo_integrated_reasoning():
    """Demonstrate integrated reasoning with provenance."""
    print("\n" + "="*70)
    print("DEMO 4: Integrated Reasoning with Provenance")
    print("="*70)
    
    # Initialize tracker
    tracker = ProvenanceTracker("Part substitution decision for P-12345")
    
    # Add source
    source_id = tracker.add_source(
        "neo4j_knowledge_graph",
        "database",
        {"query": "part_and_supplier_data"}
    )
    
    # Get data from Neo4j
    with Neo4jClient() as client:
        query = "MATCH (p:Part) WHERE p.id IN ['P-12345', 'P-67890'] RETURN p"
        parts = client.execute_query(query)
        
        if len(parts) < 2:
            print("⚠️  Parts not found. Run load_sample_data.py first.")
            return
        
        original = parts[0]['p']
        substitute = parts[1]['p']
        
        query = "MATCH (s:Supplier {id: 'SUP-001'}) RETURN s"
        suppliers = client.execute_query(query)
        supplier = suppliers[0]['s'] if suppliers else None
    
    # Create rules engine
    engine = RulesEngine()
    engine.register_rule(PartCompatibilityRule(), groups=["substitution"])
    engine.register_rule(LeadTimeFeasibilityRule(), groups=["substitution"])
    engine.register_rule(SupplierQualificationRule(), groups=["substitution"])
    
    print(f"\n📊 Evaluating substitution:")
    print(f"   Original: {original['id']} ({original['name']})")
    print(f"   Substitute: {substitute['id']} ({substitute['name']})")
    print(f"   Supplier: {supplier['name'] if supplier else 'N/A'}")
    
    # Apply compatibility rule
    compat_result = engine.apply_rule(
        "PartCompatibilityRule",
        original_part=original,
        substitute_part=substitute
    )
    
    val_id = tracker.add_validation(
        "PartCompatibilityRule",
        passed=compat_result.passed,
        reason=compat_result.reason,
        details=compat_result.details,
        parent_id=source_id
    )
    
    print(f"\n{compat_result}")
    
    # Apply lead time rule
    required_date = date.today() + timedelta(days=30)
    leadtime_result = engine.apply_rule(
        "LeadTimeFeasibilityRule",
        supplier_lead_time_days=21,
        required_date=required_date
    )
    
    tracker.add_validation(
        "LeadTimeFeasibilityRule",
        passed=leadtime_result.passed,
        reason=leadtime_result.reason,
        parent_id=source_id
    )
    
    print(f"{leadtime_result}")
    
    # Apply supplier qualification
    if supplier:
        qual_result = engine.apply_rule(
            "SupplierQualificationRule",
            supplier=supplier,
            required_certifications=['ISO9001']
        )
        
        tracker.add_validation(
            "SupplierQualificationRule",
            passed=qual_result.passed,
            reason=qual_result.reason,
            parent_id=source_id
        )
        
        print(f"{qual_result}")
    
    # Make decision
    all_passed = compat_result.passed and leadtime_result.passed
    if supplier:
        all_passed = all_passed and qual_result.passed
    
    decision = "APPROVED" if all_passed else "REJECTED"
    confidence = min(
        compat_result.confidence,
        leadtime_result.confidence,
        qual_result.confidence if supplier else 1.0
    )
    
    tracker.add_decision(
        decision=f"{decision}: Substitute {substitute['id']} for {original['id']}",
        rationale=f"All checks {'passed' if all_passed else 'failed'}",
        confidence=confidence,
        parent_ids=[val_id]
    )
    
    # Display provenance
    print(tracker.format_for_display())
    
    # Show summary
    summary = tracker.get_summary()
    print(f"\n📈 Summary:")
    print(f"   Total provenance entries: {summary['total_entries']}")
    print(f"   Final decision: {decision}")
    print(f"   Overall confidence: {confidence:.2%}")


def main():
    """Run all demos."""
    print("\n" + "="*70)
    print("REASONING ENGINE DEMONSTRATION")
    print("Supply Chain Rules & Provenance Tracking")
    print("="*70)
    
    try:
        demo_part_compatibility()
        demo_lead_time_feasibility()
        demo_supplier_qualification()
        demo_integrated_reasoning()
        
        print("\n" + "="*70)
        print("✓ All demos complete!")
        print("="*70)
        print("\nThis demonstrates the SYMBOLIC component of neuro-symbolic AI:")
        print("- Explicit rules that can be inspected and verified")
        print("- Complete provenance tracking for explainability")
        print("- Confidence scoring throughout the reasoning chain")
        print("- Production-ready validation logic")
        
    except Exception as e:
        logger.error(f"Demo failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
