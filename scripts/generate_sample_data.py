"""
Generate sample supply chain data for the knowledge graph.
"""

from datetime import date, timedelta
from typing import List, Dict, Any
import json

def generate_sample_parts() -> List[Dict[str, Any]]:
    """Generate sample parts data."""
    return [
        {
            "id": "P-12345",
            "name": "Servo Motor SM-400",
            "description": "High-torque servo motor, 400W rated power",
            "category": "electronic",
            "criticality": "HIGH",
            "unit_of_measure": "EA",
            "specifications": {
                "power_rating": "400W",
                "voltage": "24V DC",
                "torque": "1.27 Nm",
                "speed": "3000 RPM",
                "certifications": ["CE", "UL", "RoHS"],
                "dimensions": "120mm x 80mm x 60mm",
                "weight_kg": 2.5
            }
        },
        {
            "id": "P-67890",
            "name": "Servo Motor SM-450",
            "description": "High-torque servo motor, 450W rated power (upgraded model)",
            "category": "electronic",
            "criticality": "HIGH",
            "unit_of_measure": "EA",
            "specifications": {
                "power_rating": "450W",
                "voltage": "24V DC",
                "torque": "1.50 Nm",
                "speed": "3200 RPM",
                "certifications": ["CE", "UL", "RoHS", "ISO9001"],
                "dimensions": "120mm x 80mm x 60mm",
                "weight_kg": 2.6
            }
        },
        {
            "id": "P-11111",
            "name": "Mounting Bracket MB-100",
            "description": "Steel mounting bracket for motors",
            "category": "mechanical",
            "criticality": "MEDIUM",
            "unit_of_measure": "EA",
            "specifications": {
                "material": "Steel",
                "finish": "Zinc plated",
                "load_capacity_kg": 50,
                "dimensions": "150mm x 100mm x 5mm"
            }
        },
        {
            "id": "P-22222",
            "name": "Power Cable PC-24V-5M",
            "description": "24V power cable, 5 meter length",
            "category": "electrical",
            "criticality": "LOW",
            "unit_of_measure": "EA",
            "specifications": {
                "voltage_rating": "24V",
                "current_rating": "20A",
                "length_m": 5,
                "connector_type": "Phoenix",
                "certifications": ["UL", "CE"]
            }
        },
        {
            "id": "P-33333",
            "name": "Controller Board CB-2000",
            "description": "Motor controller board with CAN interface",
            "category": "electronic",
            "criticality": "CRITICAL",
            "unit_of_measure": "EA",
            "specifications": {
                "processor": "ARM Cortex-M4",
                "interfaces": ["CAN", "RS485", "Ethernet"],
                "input_voltage": "24V DC",
                "output_channels": 4,
                "certifications": ["CE", "UL", "RoHS", "IATF16949"]
            }
        }
    ]

def generate_sample_suppliers() -> List[Dict[str, Any]]:
    """Generate sample suppliers data."""
    return [
        {
            "id": "SUP-001",
            "name": "Precision Motors Inc",
            "location": "Germany",
            "contact_info": {
                "email": "procurement@precisionmotors.de",
                "phone": "+49-123-456-7890",
                "address": "Industrial Park 5, Munich, Germany"
            },
            "certifications": ["ISO9001", "ISO14001", "IATF16949"],
            "tier": 1,
            "status": "ACTIVE",
            "rating": 4.5,
            "established_date": "2020-01-01"
        },
        {
            "id": "SUP-002",
            "name": "Asia Electronics Co",
            "location": "Taiwan",
            "contact_info": {
                "email": "sales@asiaelectronics.tw",
                "phone": "+886-2-1234-5678",
                "address": "Tech District, Taipei, Taiwan"
            },
            "certifications": ["ISO9001", "ISO14001"],
            "tier": 2,
            "status": "ACTIVE",
            "rating": 4.2,
            "established_date": "2021-06-15"
        },
        {
            "id": "SUP-003",
            "name": "American Components LLC",
            "location": "USA",
            "contact_info": {
                "email": "orders@americancomponents.com",
                "phone": "+1-555-123-4567",
                "address": "123 Manufacturing Ave, Detroit, MI"
            },
            "certifications": ["ISO9001", "ITAR", "AS9100"],
            "tier": 1,
            "status": "ACTIVE",
            "rating": 4.7,
            "established_date": "2019-03-01"
        },
        {
            "id": "SUP-004",
            "name": "Euro Parts GmbH",
            "location": "Germany",
            "contact_info": {
                "email": "info@europarts.de",
                "phone": "+49-987-654-3210",
                "address": "Warehouse District, Berlin, Germany"
            },
            "certifications": ["ISO9001"],
            "tier": 2,
            "status": "ACTIVE",
            "rating": 4.0,
            "established_date": "2022-01-15"
        }
    ]

def generate_sample_supply_relationships() -> List[Dict[str, Any]]:
    """Generate sample SUPPLIES relationships."""
    today = date.today()
    
    return [
        # P-12345 (Servo Motor SM-400) suppliers
        {
            "supplier_id": "SUP-001",
            "part_id": "P-12345",
            "valid_from": str(date(2023, 1, 1)),
            "valid_to": None,
            "lead_time_days": 21,
            "price": 285.50,
            "currency": "USD",
            "min_order_quantity": 10,
            "on_time_delivery_rate": 0.92,
            "quality_rating": 4.5,
            "source": "supplier_contract_2023_001",
            "confidence": 1.0
        },
        {
            "supplier_id": "SUP-002",
            "part_id": "P-12345",
            "valid_from": str(date(2023, 6, 1)),
            "valid_to": None,
            "lead_time_days": 35,
            "price": 245.00,
            "currency": "USD",
            "min_order_quantity": 50,
            "on_time_delivery_rate": 0.88,
            "quality_rating": 4.2,
            "source": "supplier_contract_2023_002",
            "confidence": 1.0
        },
        
        # P-67890 (Servo Motor SM-450) suppliers
        {
            "supplier_id": "SUP-001",
            "part_id": "P-67890",
            "valid_from": str(date(2024, 1, 1)),
            "valid_to": None,
            "lead_time_days": 28,
            "price": 325.00,
            "currency": "USD",
            "min_order_quantity": 10,
            "on_time_delivery_rate": 0.94,
            "quality_rating": 4.6,
            "source": "supplier_contract_2024_001",
            "confidence": 1.0
        },
        
        # P-11111 (Mounting Bracket) suppliers
        {
            "supplier_id": "SUP-003",
            "part_id": "P-11111",
            "valid_from": str(date(2023, 3, 1)),
            "valid_to": None,
            "lead_time_days": 14,
            "price": 45.00,
            "currency": "USD",
            "min_order_quantity": 100,
            "on_time_delivery_rate": 0.95,
            "quality_rating": 4.7,
            "source": "supplier_contract_2023_003",
            "confidence": 1.0
        },
        {
            "supplier_id": "SUP-004",
            "part_id": "P-11111",
            "valid_from": str(date(2023, 8, 1)),
            "valid_to": None,
            "lead_time_days": 21,
            "price": 42.00,
            "currency": "USD",
            "min_order_quantity": 200,
            "on_time_delivery_rate": 0.89,
            "quality_rating": 4.0,
            "source": "supplier_contract_2023_004",
            "confidence": 1.0
        },
        
        # P-22222 (Power Cable) suppliers
        {
            "supplier_id": "SUP-002",
            "part_id": "P-22222",
            "valid_from": str(date(2023, 1, 1)),
            "valid_to": None,
            "lead_time_days": 30,
            "price": 25.00,
            "currency": "USD",
            "min_order_quantity": 100,
            "on_time_delivery_rate": 0.87,
            "quality_rating": 4.1,
            "source": "supplier_contract_2023_005",
            "confidence": 1.0
        },
        
        # P-33333 (Controller Board) suppliers
        {
            "supplier_id": "SUP-003",
            "part_id": "P-33333",
            "valid_from": str(date(2023, 1, 1)),
            "valid_to": None,
            "lead_time_days": 42,
            "price": 750.00,
            "currency": "USD",
            "min_order_quantity": 5,
            "on_time_delivery_rate": 0.93,
            "quality_rating": 4.8,
            "source": "supplier_contract_2023_006",
            "confidence": 1.0
        }
    ]

def generate_sample_compatibility() -> List[Dict[str, Any]]:
    """Generate sample part compatibility relationships."""
    return [
        {
            "original_part_id": "P-12345",
            "substitute_part_id": "P-67890",
            "compatibility_type": "FORM_FIT_FUNCTION",
            "validation_status": "VERIFIED",
            "validated_by": "engineering@company.com",
            "validated_date": "2024-01-15",
            "constraints": {
                "requires_firmware_update": True,
                "mounting_adapter_needed": False,
                "performance_note": "Improved torque and speed"
            },
            "notes": "SM-450 is drop-in replacement with better performance"
        }
    ]

def save_sample_data():
    """Save all sample data to JSON files."""
    import os
    from pathlib import Path
    
    # Create data directory
    data_dir = Path("data/sample")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Save each dataset
    datasets = {
        "parts.json": generate_sample_parts(),
        "suppliers.json": generate_sample_suppliers(),
        "supply_relationships.json": generate_sample_supply_relationships(),
        "compatibility.json": generate_sample_compatibility()
    }
    
    for filename, data in datasets.items():
        filepath = data_dir / filename
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✓ Generated {filepath}")
    
    print(f"\nSample data generated in {data_dir}/")
    print("Run 'python scripts/load_sample_data.py' to load into Neo4j")

if __name__ == "__main__":
    save_sample_data()