# Freight Domain Knowledge — GWC Lead Status Tracker
## Source: Freight Leads Assessment Report — Top 100 Questions
## Campaign: Freight March 2026 | Period: Feb 20 – Apr 6, 2026

> **Purpose**: This reference file helps Claude correctly interpret email threads when
> analysing GWC freight lead conversations (Phase 3 status tracking). Understanding what
> constitutes a "quote", a "follow-up", or a "confirmed deal" in this freight forwarding
> context is essential for accurate status transitions.

---

## How to Use This File

When running the lead-status-tracker skill (Phase 3), Claude reads CC'd email threads
and determines the lead's current status. This file provides:

1. **Domain vocabulary** — freight terms that signal specific pipeline stages
2. **Common customer questions** — what leads typically ask (helps identify engagement)
3. **Qualification signals** — what information indicates a lead is progressing
4. **Red flags** — signals that a lead is disengaged, a wrong-fit, or should be closed

---

## 1. Pipeline Stage Signals — What to Look For

### NO_ACTION → ENGAGED
Evidence that a GWC rep has responded:
- Any email FROM a `@gwclogistics.com` address TO the customer
- Rep acknowledging receipt: "Thank you for your inquiry", "received your request"
- Rep requesting more info: "please provide", "kindly share", "requesting you share"
- Rep asking for shipment details to prepare a proposal

### ENGAGED → QUOTED
Evidence that a quote/proposal was sent:
- Subject or body contains: **quotation, quote, pricing, rates, rate sheet, proposal, freight charges, our offer, costing, cost breakdown**
- Phrases: "please find attached", "herewith our quotation", "our best rates", "we can offer"
- Rep sends a document attachment described as a quote/proposal
- Mentions of specific freight charges per KG, per container, per CBM

### QUOTED → FOLLOW_UP
Evidence of customer engagement after a quote:
- Any reply FROM the customer AFTER a quote email
- Customer asking follow-up questions about the quotation
- Customer requesting clarification on rates, transit time, customs
- Customer asking: "can you confirm", "what about", "is this inclusive of"
- Note: Customer silence does NOT trigger this — there must be a reply

### FOLLOW_UP → WON_LOSS
**WON signals:**
- "confirmed", "please proceed", "go ahead", "book it", "we accept"
- "deal confirmed", "shipment confirmed", "we will proceed with GWC"
- "let's move forward", "awarded to GWC"

**LOSS signals:**
- "not interested", "cancelled", "cancel", "no longer required"
- "we have chosen another", "we went with a different company"
- "declined", "not proceeding", "found another provider"
- "unfortunately", "decided against", "budget not approved"

---

## 2. Common Customer Questions by Stage

These questions appear frequently in GWC freight email threads. Knowing these helps
Claude identify that engagement is happening even when the thread is informal.

### Early Engagement (NO_ACTION → ENGAGED)
- "How much does it cost to ship from [origin] to [destination]?"
- "Can you provide a freight quote for my shipment?"
- "What are the freight charges per container (20ft / 40ft)?"
- "What are the rates for air freight vs. ocean freight?"
- "Can you provide DDP (Delivered Duty Paid) pricing?"
- "Can you send the quotation via email or WhatsApp?"

### Documentation & Compliance Questions (common in ENGAGED stage)
- "Do I need a Commercial Registration (CR) number to ship?"
- "Is a CR number required for personal (non-commercial) shipments?"
- "What documents are needed for customs clearance?"
- "Do I need a Saudi/Qatari CR for importing to [country]?"
- "Is a trade license acceptable instead of CR?"
- "What compliance requirements exist for cross-GCC shipments?"

### Post-Quote Follow-up Questions (QUOTED → FOLLOW_UP)
- "Is customs clearance included in the freight price?"
- "What is the transit time for ocean freight from [origin] to [destination]?"
- "Are there additional charges like waiting, demurrage, or storage fees?"
- "Can you provide rates for recurring/regular shipments?"
- "What is the cost for door-to-door delivery including customs?"
- "Can you match another company's price?"
- "How quickly can you arrange shipping / what is the ETD?"

---

## 3. Top Shipping Corridors (by inquiry volume)

Knowing these corridors helps Claude confirm the lead context is freight-related:

| Rank | Origin | Destination |
|------|--------|-------------|
| 1 | China | Qatar |
| 2 | India | UAE |
| 3 | UAE | Qatar |
| 4 | China | Saudi Arabia |
| 5 | UAE | Saudi Arabia |
| 6 | India | Qatar |
| 7 | China | UAE |
| 8 | China | Bahrain |
| 9 | Pakistan | Qatar/UAE |
| 10 | Saudi Arabia | Qatar |

---

## 4. Top Product Categories

Typical freight commodities in GWC lead emails:
- **Food & Agriculture**: Onions, rice, spices, vegetables, frozen meat, fruits
- **Spare Parts**: Auto parts, machinery parts, elevator parts
- **Personal Items & Furniture**: Household goods, furniture, personal effects
- **Vehicles**: Cars, motorcycles
- **Electronics**: Mobile accessories, solar panels, LED lights
- **Building Materials**: Marble, granite, stone, wood, steel
- **Consumer Goods**: Gym equipment, perfumes, clothing
- **Industrial Materials**: Chemicals, oil, machinery

---

## 5. Unqualified Lead Signals (Not a Status Transition — Flag for Review)

These patterns in email threads suggest the lead is NOT a viable freight customer.
Do NOT transition to WON/LOSS — flag as a note in the analysis reasoning:

- **Partnership/broker requests**: "Can we collaborate?", "I want to work WITH GWC", "subcontracting", "agent partnership"
- **Job seekers**: Mentions of CV, resume, employment application
- **Personal shipments with no CR**: Explicitly states "no company", "personal use", "no business registration"
- **Wrong service**: Inquiring about e-commerce or relocation services (out of scope for freight pipeline)
- **Duplicate contacts**: Same company/contact appearing multiple times
- **Price-shoppers with no intent**: "Just wanted to know the price" with no follow-through after receiving a quote

---

## 6. Freight Terminology Glossary

Key terms to correctly interpret email threads:

| Term | Meaning |
|------|---------|
| **LCL** | Less-than-Container-Load (shared container, billed by CBM/kg) |
| **FCL** | Full Container Load (20ft or 40ft) |
| **BBK** | Break Bulk (large/heavy cargo outside containers) |
| **RORO** | Roll-on/Roll-off (vehicles, machinery on wheels) |
| **LTL** | Less-than-Truckload (road freight) |
| **FTL** | Full Truckload (road freight) |
| **ETD** | Estimated Time of Departure |
| **ETA** | Estimated Time of Arrival |
| **DDP** | Delivered Duty Paid (seller covers customs + delivery) |
| **Incoterms** | Shipping contract terms (EXW, FOB, CIF, DDP, DAP, etc.) |
| **CBM** | Cubic Metre (volume measurement) |
| **Chargeable weight** | Greater of actual weight or volumetric weight (used for billing) |
| **DG / Dangerous Goods** | Hazardous materials requiring special handling |
| **MSDS** | Material Safety Data Sheet (required for DG cargo) |
| **CR number** | Commercial Registration — required for business shipments |
| **Customs clearance** | Formal process of getting goods through customs |
| **Port of loading / POL** | Departure port |
| **Port of discharge / POD** | Arrival port |
| **Hamad Port** | Qatar's main container port |
| **Jebel Ali** | UAE's main container port (Dubai) |

---

## 7. Key Friction Points to Recognise in Email Threads

These are not disqualifiers — they are normal friction that reps must work through:

1. **CR Number resistance** — Many leads don't have or refuse to share CR numbers. A rep asking for CR details and a customer hesitating does NOT mean the lead is lost. Continue tracking.

2. **Price-before-details** — Customer wants pricing before providing shipment details. This is common (25+ leads). Rep should still attempt to gather details.

3. **Language barriers** — Threads that switch languages or are brief may indicate communication difficulty — not necessarily disengagement.

4. **No-answer pattern** — If a rep emails but gets no reply for 5+ days → flag as dark lead.

5. **Call hang-ups** — Not visible in email threads. If email thread mentions "tried to reach you", "attempted contact", this is a follow-up attempt — status likely stays ENGAGED.

---

## 8. GWC-Specific Instructions in Rep Emails

All automated routing emails instruct reps to:
- **Include the GWC ID in every subject line** (e.g. `Re: Freight Inquiry GWC-741228136654`)
- **CC `Sales.rfq@gwclogistics.com`** on all customer correspondence

When scanning threads, look for subject lines containing `GWC-\d+` to confirm the thread
belongs to this lead. If a rep email does NOT include the GWC ID in the subject, it may
still be part of the thread — check the sender (`@gwclogistics.com`) and recipient context.

---

