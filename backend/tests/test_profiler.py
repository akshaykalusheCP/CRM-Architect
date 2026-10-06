from app.ingestion.profiler import profile_tabular
from app.ingestion.relationships import infer_relationships

MESSY_CSV = """SunPeak Solar - Lead register,,,,,
Exported 03/04/2025,,,,,
Client Name,Email,Mobile No,Enquiry Date,Status,Amount
Ravi Kumar,ravi@example.com,+91 98765 43210,12/01/2025,New,"150,000"
Asha Patel,asha@example.com,+91 91234 56780,15/01/2025,Qualified,"220,000"
Asha Patel,asha@example.com,+91 91234 56780,15/01/2025,Qualified,"220,000"
John D'Souza,john@example.org,+91 99887 76655,20/01/2025,New,"90,000"
Meera Iyer,meera@example.in,+91 90000 11111,22/01/2025,Lost,"175,000"
Kiran Rao,kiran@example.com,+91 98989 89898,25/01/2025,New,"300,000"
Sanjay Gupta,sanjay@example.com,+91 97777 66666,28/01/2025,Won,"410,000"
Pooja Nair,pooja@example.com,+91 95555 44444,02/02/2025,Qualified,"125,000"
Arjun Menon,arjun@example.com,+91 93333 22222,05/02/2025,New,"260,000"
Divya Shah,divya@example.com,+91 92222 11111,09/02/2025,Won,"198,000"
"""


def test_profile_handles_junk_header_rows_and_types():
    profile = profile_tabular("leads.csv", MESSY_CSV.encode(), max_rows=1000, mask_pii=True)
    sheet = profile["sheets"][0]
    assert sheet["header_row"] == 3
    assert sheet["row_count"] == 10
    cols = {c["name"]: c for c in sheet["columns"]}
    assert cols["Email"]["inferred_type"] == "email"
    assert cols["Mobile No"]["inferred_type"] == "phone"
    assert cols["Enquiry Date"]["inferred_type"] == "date"
    assert cols["Status"]["inferred_type"] == "picklist"
    assert cols["Amount"]["inferred_type"] == "currency"
    # PII masked before anything reaches the LLM
    assert all("***" in s for s in cols["Email"]["samples"])
    assert {d["column"] for d in sheet["possible_duplicates"]} >= {"Email"}


def test_relationship_inference():
    rels = infer_relationships(
        {
            ("orders.csv", "Sheet1", "Customer Code"): {"c1", "c2", "c3"},
            ("customers.csv", "Sheet1", "Code"): {"c1", "c2", "c3", "c4"},
            ("customers.csv", "Sheet1", "City"): {"pune", "mumbai", "delhi"},
        }
    )
    assert rels[0]["from"] == "orders.csv > Sheet1 > Customer Code"
    assert rels[0]["to"] == "customers.csv > Sheet1 > Code"
