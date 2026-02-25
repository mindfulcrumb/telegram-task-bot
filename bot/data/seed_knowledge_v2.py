"""Seed knowledge base v2 — regulatory data, interactions, stacking, new compounds, evidence tiers.

Runs on startup AFTER seed_knowledge.py. Uses ALTER/UPDATE so it's safe to run repeatedly.
"""
import logging
from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def _table_has_data(cur, table: str) -> bool:
    cur.execute(f"SELECT EXISTS(SELECT 1 FROM {table} LIMIT 1)")
    return cur.fetchone()[0]


# ═══════════════════════════════════════════════════════════════════════
# EVIDENCE TIER DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

def seed_evidence_tiers():
    with get_cursor(dict_cursor=False) as cur:
        if _table_has_data(cur, "evidence_tiers"):
            return
        tiers = [
            ("A", "Strong Clinical Evidence",
             "FDA-approved or multiple randomized controlled trials in humans. Well-established dosing and safety profile.",
             "Semaglutide, HGH, PT-141, Sermorelin, Thymosin Alpha-1"),
            ("B", "Moderate Clinical Evidence",
             "Phase II/III trials, strong preclinical data, or extensive clinical use in anti-aging medicine. Dosing based on clinical experience.",
             "BPC-157, TB-500, CJC-1295 + Ipamorelin, GHK-Cu, NAD+"),
            ("C", "Emerging/Preclinical Evidence",
             "Promising preclinical data, limited human studies. Dosing from practitioner protocols. Use with medical supervision.",
             "MOTS-c, Epithalon, DSIP, SS-31, KPV"),
            ("D", "Early Research",
             "Animal studies only or very limited human data. Experimental dosing. High uncertainty on safety/efficacy.",
             "FOXO4-DRI, SLU-PP-332, ARA-290"),
            ("E", "Theoretical/Pre-research",
             "Concept stage or very early bench research. No established human protocols. Not recommended for general use.",
             "GLP-3 Research compounds"),
        ]
        for tier, label, desc, examples in tiers:
            cur.execute(
                """INSERT INTO evidence_tiers (tier, label, description, examples)
                   VALUES (%s, %s, %s, %s) ON CONFLICT (tier) DO NOTHING""",
                (tier, label, desc, examples),
            )
        logger.info(f"Seeded {len(tiers)} evidence tier definitions")


# ═══════════════════════════════════════════════════════════════════════
# FDA REGULATORY STATUS + WADA STATUS FOR ALL PEPTIDES
# ═══════════════════════════════════════════════════════════════════════

def seed_regulatory_status():
    """Update all existing peptides with FDA status, WADA status, and legal notes."""
    with get_cursor(dict_cursor=False) as cur:
        # Check if we've already done this
        cur.execute("SELECT fda_status FROM peptide_reference WHERE slug = 'bpc-157' LIMIT 1")
        row = cur.fetchone()
        if row and row[0] != 'unregulated':
            logger.info("Regulatory status already seeded, skipping")
            return

        # (slug, fda_status, wada_prohibited, wada_category, legal_notes)
        regulatory = [
            # FDA-approved peptides
            ("glp-1s", "fda_approved", False, None,
             "Semaglutide (Wegovy/Ozempic) and Tirzepatide (Mounjaro/Zepbound) are FDA-approved. Compounded versions restricted after shortage resolution (2025)."),
            ("pt-141", "fda_approved", False, None,
             "FDA-approved as Vyleesi (bremelanotide) for HSDD in premenopausal women. Prescription required."),
            ("hcg", "fda_approved", False, None,
             "FDA-approved for fertility and hypogonadism. Prescription required. Compounding restricted since 2020."),
            ("hgh-somatropin", "fda_approved", True, "S2 Peptide Hormones",
             "FDA-approved for GH deficiency. Schedule III controlled substance. Prescription required. Off-label anti-aging use common."),
            ("tesamorelin", "fda_approved", True, "S2 GH Releasing Factors",
             "FDA-approved as Egrifta for HIV lipodystrophy. Off-label use for visceral fat reduction. WADA prohibited."),
            ("sermorelin", "fda_approved", True, "S2 GH Releasing Factors",
             "Previously FDA-approved GHRH analog. Discontinued commercially but available through compounding. WADA prohibited."),
            ("thymosin-alpha-1", "approved_other_countries", False, None,
             "Approved in 30+ countries (Zadaxin). Not FDA-approved in US. Sept 2024: REMOVED from FDA Category 2 (can be compounded). Strong safety record."),

            # FDA Category 2 — safety concerns (as of Feb 2026)
            ("bpc-157", "fda_category_2", False, None,
             "FDA placed on Category 2 list (substances with safety concerns) — CANNOT be compounded in the US. Available through research channels. Not explicitly banned by WADA but falls under S0 general clause."),
            ("tb-500", "fda_category_2", True, "S2 Peptide Hormones",
             "FDA Category 2 — cannot be compounded in US. WADA prohibited under Thymosin Beta-4."),

            # Sept 2024: REMOVED from Category 2 (CAN be compounded)
            ("cjc-1295-no-dac", "compoundable", True, "S2 GH Releasing Factors",
             "Sept 2024: REMOVED from FDA Category 2. Can be legally compounded by 503A/503B pharmacies. WADA prohibited."),
            ("cjc-1295-ipamorelin", "compoundable", True, "S2 GH Releasing Factors",
             "Sept 2024: Both CJC-1295 and Ipamorelin REMOVED from FDA Category 2. Legal to compound. WADA prohibited."),
            ("ipamorelin", "compoundable", True, "S2 GH Releasing Factors",
             "Sept 2024: REMOVED from FDA Category 2. Legal to compound by 503A/503B pharmacies. WADA prohibited as GH secretagogue."),
            ("selank", "compoundable", False, None,
             "Sept 2024: REMOVED from FDA Category 2. Legal to compound. Approved pharmaceutical in Russia. Not a performance enhancer."),

            # WADA prohibited GHRPs
            ("ghrp-2", "unregulated", True, "S2 GH Releasing Peptides",
             "Not FDA-approved. WADA prohibited. Available through research/compounding channels."),
            ("ghrp-6", "unregulated", True, "S2 GH Releasing Peptides",
             "Not FDA-approved. WADA prohibited. Available through research/compounding channels."),

            # Unregulated / research peptides
            ("aod-9604", "unregulated", False, None,
             "FDA GRAS status for food use. Not FDA-approved as drug. Available through compounding."),
            ("bpc-157-tb-500-blend", "fda_category_2", True, "S2 Peptide Hormones",
             "Contains Category 2 compounds (BPC-157 + TB-500). Cannot be compounded in US."),
            ("bpc-157-capsules", "fda_category_2", False, None,
             "Oral BPC-157 falls under same Category 2 restriction as injectable form."),
            ("dsip", "unregulated", False, None,
             "Not scheduled or regulated in most countries. Available as research peptide."),
            ("epithalon", "unregulated", False, None,
             "Unregulated research peptide. No FDA status. Available through peptide suppliers."),
            ("foxo4-dri", "unregulated", False, None,
             "Experimental senolytic. No regulatory status. Research only."),
            ("ghk-cu", "unregulated", False, None,
             "Copper peptide used in cosmetics (topical is OTC). Injectable form is unregulated."),
            ("glutathione", "supplement", False, None,
             "Available as OTC supplement (oral) and compounded injectable. IV form through clinics. No FDA approval needed for supplement form."),
            ("igf-1-lr3", "unregulated", True, "S2 Peptide Hormones",
             "Not FDA-approved. WADA prohibited under IGF-1 and its analogs. Research peptide."),
            ("hgh-frag-176-191", "unregulated", True, "S2 Peptide Hormones",
             "Not FDA-approved. WADA prohibited. Same class as AOD-9604."),
            ("kisspeptin", "unregulated", False, None,
             "Research peptide used in fertility clinics. Not FDA-approved for general use."),
            ("kpv", "unregulated", False, None,
             "Research peptide. Not FDA-approved. Derived from alpha-MSH."),
            ("ll-37", "unregulated", False, None,
             "Human antimicrobial peptide. Not FDA-approved as drug. Research/compounding availability."),
            ("melanotan-i", "unregulated", False, None,
             "FDA-approved version (afamelanotide/Scenesse) for EPP only. Research peptide form unregulated."),
            ("melanotan-ii", "unregulated", False, None,
             "Unregulated research peptide. Banned in Australia. Not FDA-approved."),
            ("mgf", "unregulated", True, "S2 Peptide Hormones",
             "Not FDA-approved. WADA prohibited under growth factors."),
            ("peg-mgf", "unregulated", True, "S2 Peptide Hormones",
             "Not FDA-approved. WADA prohibited under growth factors."),
            ("mots-c", "unregulated", False, None,
             "Mitochondrial peptide. Research stage. No FDA status."),
            ("nad-plus", "supplement", False, None,
             "NAD+ precursors (NMN, NR) available as supplements. IV NAD+ through clinics. Injectable through compounding."),
            ("oxytocin", "fda_approved", False, None,
             "FDA-approved for labor induction (Pitocin). Intranasal form off-label through compounding."),
            ("semax", "approved_other_countries", False, None,
             "Approved pharmaceutical in Russia. Not FDA-approved in US. Available through compounding."),
            ("ss-31", "clinical_trial", False, None,
             "Phase II/III clinical trials as elamipretide (Stealth BioTherapeutics). Not yet FDA-approved."),
            ("mots-c", "unregulated", False, None,
             "Research peptide. No regulatory status."),
            ("5-amino-1mq", "unregulated", False, None,
             "Research compound. No FDA status."),
            ("ara-290", "clinical_trial", False, None,
             "Phase II trials for neuropathy and sarcoidosis. Not yet approved."),
            ("cagrilintide", "clinical_trial", False, None,
             "Phase III trials by Novo Nordisk. Part of CagriSema combination. Not yet FDA-approved."),
            ("mazdutide", "clinical_trial", False, None,
             "Phase III trials in China and globally. Not yet FDA-approved."),
            ("slu-pp-332", "unregulated", False, None,
             "Early research compound from Washington University. Not available for human use."),
            ("slu-pp-332-capsules", "unregulated", False, None,
             "Early research compound. Not available for human use."),
            ("glp-2t", "fda_approved", False, None,
             "GLP-2 analog teduglutide (Gattex) FDA-approved for short bowel syndrome. Other GLP-2 forms are research."),
            ("glp-3r", "unregulated", False, None,
             "Theoretical research. No compounds available."),
            ("glow-blend", "unregulated", False, None,
             "Proprietary blend. Regulatory status depends on components."),
            ("klow-blend", "unregulated", False, None,
             "Proprietary blend. Regulatory status depends on components."),
        ]
        for slug, fda, wada, wada_cat, notes in regulatory:
            cur.execute(
                """UPDATE peptide_reference
                   SET fda_status = %s, wada_prohibited = %s, wada_category = %s,
                       legal_notes = %s, last_updated = NOW()
                   WHERE slug = %s""",
                (fda, wada, wada_cat, notes, slug),
            )
        logger.info(f"Updated regulatory status for {len(regulatory)} peptides")


# ═══════════════════════════════════════════════════════════════════════
# NEW PEPTIDE COMPOUNDS (missing from original 47)
# ═══════════════════════════════════════════════════════════════════════

def seed_new_peptides():
    """Add missing peptide compounds discovered in gap analysis."""
    with get_cursor(dict_cursor=False) as cur:
        # Check if first new peptide exists
        cur.execute("SELECT 1 FROM peptide_reference WHERE slug = 'retatrutide' LIMIT 1")
        if cur.fetchone():
            logger.info("New peptides already seeded, skipping")
            return

        new_peptides = [
            ("retatrutide", "Retatrutide", "Triple agonist (GIP/GLP-1/glucagon) — strongest weight loss peptide in clinical trials",
             "Simultaneously activates GIP, GLP-1, and glucagon receptors for enhanced metabolic effects and thermogenesis",
             ["Record weight loss (24% in trials)", "Improved glycemic control", "Hepatic fat reduction", "Cardiovascular benefits"],
             ["metabolic"], ["Subcutaneous"], "1-12mg", "Weekly (titrate up)", "Ongoing",
             "Titrate slowly: 0.5mg -> 12mg over 36 weeks. Phase III underway.",
             ["Cagrilintide"], ["Nausea", "Diarrhea", "Vomiting", "Constipation"],
             ["MTC/MEN2", "Pancreatitis history", "Severe GI disease"],
             "B", "Phase II TRIUMPH-2 trial showed 24.2% weight loss at 48 weeks — highest of any anti-obesity medication. Phase III underway by Eli Lilly.",
             "6 days", False, "clinical_trial", False, None,
             "Phase III trials by Eli Lilly. Not yet FDA-approved. If approved, would be strongest weight loss peptide available."),

            ("survodutide", "Survodutide", "Dual glucagon/GLP-1 receptor agonist for obesity and MASH (fatty liver)",
             "Activates glucagon receptor (increases energy expenditure) and GLP-1 receptor (reduces appetite) simultaneously",
             ["Significant weight loss", "MASH/NAFLD improvement", "Hepatic fat reduction", "Improved liver biomarkers"],
             ["metabolic"], ["Subcutaneous"], "0.3-4.8mg", "Weekly (titrate up)", "Ongoing",
             "Titrate slowly. Unique glucagon component targets liver fat directly.",
             [], ["Nausea", "Diarrhea", "Vomiting"],
             ["MTC/MEN2", "Pancreatitis history"],
             "B", "Phase III trials for obesity (SYNCHRONIZE) and MASH (FALCON). Up to 19% weight loss. Unique liver fat targeting via glucagon receptor.",
             "5 days", False, "clinical_trial", False, None,
             "Phase III by Boehringer Ingelheim. Particularly promising for MASH/NAFLD."),

            ("humanin", "Humanin", "Mitochondrial-derived peptide with neuroprotective and anti-aging properties",
             "Encoded in mitochondrial DNA, signals cellular stress response, inhibits apoptosis via interaction with BAX and IGFBP-3",
             ["Neuroprotection", "Anti-aging", "Insulin sensitization", "Cardiac protection", "Anti-inflammatory"],
             ["longevity", "cognition"], ["Subcutaneous"], "1-5mg", "3x per week", "8-12 weeks",
             "Related to MOTS-c (both mitochondrial-derived). Levels decline with age.",
             ["MOTS-c", "NAD+", "SS-31"], ["Mild injection site reactions"],
             ["Limited safety data", "Active cancer (theoretical)"],
             "C", "Discovered in 2001. Associated with exceptional longevity in centenarians. Shows protection against Alzheimer's, diabetes, and cardiovascular disease in preclinical models.",
             "4-6 hours", False, "unregulated", False, None,
             "Research peptide. No regulatory status. Mitochondrial-derived, studied in longevity research."),

            ("dihexa", "Dihexa", "Potent nootropic peptide acting through HGF/c-Met receptor system for cognitive enhancement",
             "Activates hepatocyte growth factor (HGF) / c-Met receptor signaling, promoting synaptogenesis and dendritic spine formation",
             ["Powerful cognitive enhancement", "Memory improvement", "Synapse formation", "Potential neurodegeneration treatment"],
             ["cognition"], ["Oral", "Intranasal", "Subcutaneous"], "10-40mg oral", "Daily", "4-8 weeks",
             "Extremely potent — 10 million times more potent than BDNF at enhancing synaptic connectivity in preclinical models. Oral bioavailability uncertain.",
             ["Semax", "Selank"], ["Headache", "Overstimulation", "Unknown long-term effects"],
             ["Cancer history (HGF/c-Met involved in tumor growth)", "Pregnancy"],
             "D", "Washington State University research shows dramatic cognitive improvement in animal models. No human clinical trials. HGF/c-Met pathway involvement raises theoretical cancer concerns.",
             "2-4 hours", False, "unregulated", False, None,
             "Research compound only. No human trials. Theoretical cancer risk due to c-Met pathway involvement. Use with extreme caution."),

            ("pe-22-28", "PE-22-28", "Nootropic peptide derived from spadin that enhances neurogenesis via TREK-1 channel modulation",
             "Blocks TREK-1 potassium channels, increasing neuronal excitability and promoting BDNF-mediated neurogenesis in hippocampus",
             ["Antidepressant effects", "Cognitive enhancement", "Neurogenesis", "Anxiolytic"],
             ["cognition"], ["Intranasal", "Subcutaneous"], "200-500mcg intranasal", "1-2x daily", "2-4 weeks",
             "Derived from spadin. Acts within days vs weeks for traditional antidepressants. Intranasal preferred for brain delivery.",
             ["Semax", "Selank", "DSIP"], ["Mild headache", "Nasal irritation"],
             ["Very limited safety data"],
             "D", "Preclinical studies show rapid antidepressant effects (3-4 days vs 3 weeks for SSRIs). Promotes hippocampal neurogenesis. Very limited human data.",
             "1-2 hours", False, "unregulated", False, None,
             "Research peptide. No human clinical trials. Mechanism well-understood but safety data limited."),

            ("vip", "VIP (Vasoactive Intestinal Peptide)", "Neuropeptide with powerful anti-inflammatory and immune-modulatory properties, used in long COVID protocols",
             "Binds VPAC1/VPAC2 receptors, inhibiting inflammatory cytokines, protecting pulmonary surfactant, and modulating T-cell function",
             ["Pulmonary protection", "Anti-inflammatory", "Immune modulation", "Neuroprotection", "Long COVID treatment"],
             ["immunity", "recovery"], ["Intranasal", "Subcutaneous", "Inhaled"], "50-100mcg intranasal", "2-3x daily", "4-12 weeks",
             "Dr. Shoemaker protocol for CIRS/mold illness. Increasingly used for long COVID pulmonary symptoms. Intranasal most common.",
             ["Thymosin Alpha-1", "BPC-157", "LL-37"], ["Nasal irritation", "Diarrhea (high doses)", "Flushing"],
             ["Active heart failure", "History of VIPoma"],
             "C", "Used clinically for CIRS by Dr. Shoemaker since 2010s. Phase II data for pulmonary hypertension. Growing use in long COVID respiratory protocols.",
             "1-2 minutes (short-acting)", False, "unregulated", False, None,
             "Research peptide. Compounding available. Dr. Shoemaker CIRS protocol. Emerging long COVID applications."),

            ("cerebrolysin", "Cerebrolysin", "Neurotrophic peptide preparation derived from porcine brain tissue for cognitive recovery",
             "Contains brain-derived neurotrophic factors (BDNF, GDNF, NGF-like peptides) that promote neuroplasticity, synaptic repair, and neurogenesis",
             ["Stroke recovery", "TBI treatment", "Cognitive enhancement", "Neuroprotection", "Dementia support"],
             ["cognition", "recovery"], ["Intramuscular", "IV"], "5-30mL IM or IV", "Daily for 10-20 days", "10-20 day cycles, repeat as needed",
             "Used extensively in Europe and Asia for neurological conditions. Cycled in 10-20 day courses. IM injection is practical; IV for acute cases.",
             ["Semax", "NAD+"], ["Injection site reactions", "Dizziness", "Headache", "Agitation (rare)"],
             ["Known brain tumors", "Epilepsy (caution)", "Renal insufficiency"],
             "B", "Approved in 40+ countries for stroke, TBI, and dementia. Meta-analyses show improvement in stroke recovery and cognitive scores. Not FDA-approved in US.",
             "Variable (peptide mixture)", False, "approved_other_countries", False, None,
             "Approved in 40+ countries. Not FDA-approved in US. Available through international pharmacies. Strong clinical evidence for neurological recovery."),

            ("klotho", "Klotho Peptide", "Anti-aging peptide based on klotho protein — associated with exceptional longevity and cognitive function",
             "Klotho protein modulates FGF23 signaling, Wnt pathway, insulin/IGF-1 signaling, and oxidative stress response",
             ["Longevity extension", "Cognitive enhancement", "Kidney protection", "Anti-inflammatory", "Metabolic regulation"],
             ["longevity", "cognition"], ["Subcutaneous", "IV"], "Research doses", "Research protocols", "Research only",
             "Very early stage. Klotho protein levels decline sharply with age and correlate with lifespan. Single injection improved cognition in aged mice for 2 weeks.",
             ["Epithalon", "NAD+", "MOTS-c"], ["Limited safety data"],
             ["Research only — no established safety profile"],
             "D", "UCSF research (Dubal lab) showed single injection of klotho fragment improved cognition in young and old mice. Human klotho levels predict longevity. Therapeutic peptide in early development.",
             "Unknown", False, "unregulated", False, None,
             "Very early research. No human clinical trials. Based on klotho longevity gene research. One of the most promising theoretical anti-aging targets."),

            ("tirzepatide", "Tirzepatide", "Dual GIP/GLP-1 receptor agonist — FDA-approved for diabetes and obesity with superior efficacy",
             "Simultaneously activates GIP and GLP-1 receptors, producing enhanced insulin secretion, appetite suppression, and metabolic improvement",
             ["Superior weight loss (22.5% in SURMOUNT)", "Best-in-class glycemic control", "Cardiovascular benefits", "MASH improvement"],
             ["metabolic"], ["Subcutaneous"], "2.5-15mg", "Weekly (titrate up every 4 weeks)", "Ongoing",
             "Start 2.5mg, titrate to 15mg. FDA-approved as Mounjaro (T2D) and Zepbound (obesity). Maintain high protein intake.",
             ["Cagrilintide"], ["Nausea", "Diarrhea", "Constipation", "Injection site reactions", "Pancreatitis (rare)"],
             ["MTC/MEN2", "Pancreatitis history", "Severe GI disease", "Gastroparesis"],
             "A", "SURMOUNT-1 showed 22.5% weight loss at 72 weeks. SURPASS trials show superior HbA1c reduction vs semaglutide. FDA-approved for T2D and obesity.",
             "5 days", False, "fda_approved", False, None,
             "FDA-approved as Mounjaro (2022, T2D) and Zepbound (2023, obesity). Lilly. Compounding restricted after shortage resolution."),
        ]

        for p in new_peptides:
            (slug, name, desc, mech, benefits, cats, routes, dose, freq, dur, notes,
             stacks, sides, contras, ev, research, hl, beginner,
             fda, wada, wada_cat, legal) = p
            sv_text = f"{name} {slug} {desc} {mech} {' '.join(benefits)} {' '.join(cats)}"
            cur.execute(
                """INSERT INTO peptide_reference
                   (slug, name, description, mechanism, benefits, categories, routes,
                    standard_dose, standard_frequency, standard_duration, dosage_notes,
                    stack_suggestions, side_effects, contraindications, evidence_level,
                    research_summary, half_life, beginner_friendly, search_vector,
                    fda_status, wada_prohibited, wada_category, legal_notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           to_tsvector('english', %s), %s,%s,%s,%s)
                   ON CONFLICT (slug) DO NOTHING""",
                (slug, name, desc, mech, benefits, cats, routes, dose, freq, dur, notes,
                 stacks, sides, contras, ev, research, hl, beginner, sv_text,
                 fda, wada, wada_cat, legal),
            )
        logger.info(f"Seeded {len(new_peptides)} new peptide compounds")


# ═══════════════════════════════════════════════════════════════════════
# PEPTIDE INTERACTIONS — cross-compound warnings
# ═══════════════════════════════════════════════════════════════════════

def seed_peptide_interactions():
    with get_cursor(dict_cursor=False) as cur:
        if _table_has_data(cur, "peptide_interactions"):
            logger.info("peptide_interactions already seeded, skipping")
            return

        # (peptide_a, peptide_b, interaction_type, severity, description, mechanism, recommendation, source)
        interactions = [
            # GH secretagogue stacking
            ("CJC-1295", "Ipamorelin", "synergistic", "info",
             "Gold standard synergy — CJC-1295 amplifies GHRH while Ipamorelin triggers clean GH pulse",
             "Complementary receptor targeting: GHRH receptor + ghrelin receptor",
             "Combine at bedtime for maximum GH pulse. Standard: 100mcg each.",
             "Clinical practice consensus"),

            ("CJC-1295", "GHRP-2", "synergistic", "caution",
             "Synergistic GH release but GHRP-2 elevates cortisol and prolactin unlike Ipamorelin",
             "Both stimulate GH but via different receptors. GHRP-2 is less selective.",
             "Prefer CJC-1295 + Ipamorelin for cleaner profile. Use GHRP-2 only if stronger GH pulse needed.",
             "Dr. Seeds protocols"),

            # GH secretagogues + HGH
            ("Ipamorelin", "HGH (Somatropin)", "redundant", "caution",
             "Both increase GH levels — combining may cause excessive GH/IGF-1 elevation",
             "Exogenous GH + GH secretagogue = supraphysiological GH levels",
             "Use one or the other, not both. If using HGH, secretagogues are unnecessary.",
             "Clinical practice"),

            # Recovery stack synergies
            ("BPC-157", "TB-500", "synergistic", "info",
             "Complementary healing: BPC-157 for local angiogenesis, TB-500 for systemic inflammation reduction",
             "BPC-157 upregulates VEGF locally. TB-500 activates stem cells systemically.",
             "Gold standard recovery stack. BPC-157 250mcg daily + TB-500 2-5mg 2x/week.",
             "Sports medicine practice"),

            ("BPC-157", "GHK-Cu", "synergistic", "info",
             "Complementary tissue repair: BPC-157 for deep tissue, GHK-Cu for collagen and skin",
             "Different repair mechanisms that don't compete.",
             "Good combination for injury recovery with skin/scar healing component.",
             "Clinical practice"),

            # GLP-1 interactions
            ("Semaglutide", "Tirzepatide", "contraindicated", "warning",
             "DO NOT combine two GLP-1 agonists — risk of severe GI side effects and pancreatitis",
             "Overlapping GLP-1 receptor activation with additive side effects",
             "Choose one GLP-1 agonist. Switch between them with washout period (1-2 weeks).",
             "FDA prescribing information"),

            ("Semaglutide", "BPC-157", "beneficial", "info",
             "BPC-157 may help with GLP-1-induced GI side effects through gut healing",
             "BPC-157 heals gut lining while semaglutide can cause GI inflammation",
             "Consider oral BPC-157 500mcg if experiencing significant GI distress on GLP-1s.",
             "Practitioner experience"),

            # PT-141 interactions
            ("PT-141 (Bremelanotide)", "Melanotan II", "contraindicated", "warning",
             "Both are melanocortin agonists — overlapping receptor activation increases side effect risk",
             "Both activate MC3R/MC4R. Additive nausea, blood pressure effects.",
             "Do not combine. Choose one based on primary goal (libido vs tanning).",
             "Pharmacology"),

            # Immune peptides
            ("Thymosin Alpha-1", "LL-37", "synergistic", "info",
             "Complementary immune enhancement: TA-1 for adaptive immunity, LL-37 for innate antimicrobial defense",
             "TA-1 enhances T-cells and NK cells. LL-37 directly kills pathogens and breaks biofilms.",
             "Good combination for chronic infections or immune support protocols.",
             "Immune peptide protocols"),

            # Nootropic peptides
            ("Semax", "Selank", "synergistic", "info",
             "Russian clinical combination: Semax for cognitive enhancement, Selank for anxiolytic balance",
             "Semax boosts BDNF/focus. Selank modulates GABA/serotonin for calm.",
             "Classic nootropic stack. Semax AM for focus, Selank PM or as needed for anxiety.",
             "Russian pharmaceutical practice"),

            # Sleep peptides
            ("DSIP", "Ipamorelin", "synergistic", "info",
             "Both improve sleep quality — DSIP enhances deep sleep, Ipamorelin promotes GH during sleep",
             "Complementary sleep architecture improvement.",
             "Take both before bed. DSIP 100-200mcg + Ipamorelin 100-200mcg.",
             "Sleep optimization protocols"),

            # Longevity combinations
            ("Epithalon", "NAD+", "synergistic", "info",
             "Complementary longevity: Epithalon extends telomeres, NAD+ supports sirtuin-mediated DNA repair",
             "Different anti-aging mechanisms with no known interference.",
             "Good longevity stack. Epithalon in cycles + NAD+ maintenance.",
             "Anti-aging protocols"),

            ("MOTS-c", "SS-31", "synergistic", "info",
             "Dual mitochondrial support: MOTS-c activates AMPK, SS-31 stabilizes cardiolipin",
             "Both target mitochondria through different mechanisms.",
             "Complementary mitochondrial optimization stack.",
             "Mitochondrial research"),

            # GLP-1 + GH secretagogue
            ("Semaglutide", "CJC-1295 + Ipamorelin", "beneficial", "caution",
             "GH secretagogues may help preserve muscle mass during GLP-1-induced weight loss",
             "GLP-1s cause 30-40% lean mass loss. GH promotes muscle preservation.",
             "Consider adding GH secretagogues during GLP-1 therapy to preserve muscle. Monitor IGF-1.",
             "Clinical practice — Dr. Seeds"),

            # Insulin sensitivity concerns
            ("HGH (Somatropin)", "Semaglutide", "beneficial", "caution",
             "GH can worsen insulin resistance while GLP-1 improves it — may partially counteract",
             "HGH reduces insulin sensitivity. GLP-1 agonists improve it.",
             "Monitor glucose closely when combining. GLP-1 may offset GH's diabetogenic effect.",
             "Endocrinology"),
        ]

        for a, b, itype, sev, desc, mech, rec, src in interactions:
            cur.execute(
                """INSERT INTO peptide_interactions
                   (peptide_a, peptide_b, interaction_type, severity, description,
                    mechanism, recommendation, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (peptide_a, peptide_b, interaction_type) DO NOTHING""",
                (a, b, itype, sev, desc, mech, rec, src),
            )
        logger.info(f"Seeded {len(interactions)} peptide interactions")


# ═══════════════════════════════════════════════════════════════════════
# STACKING PROTOCOLS — curated compound combinations
# ═══════════════════════════════════════════════════════════════════════

def seed_stacking_protocols():
    with get_cursor(dict_cursor=False) as cur:
        if _table_has_data(cur, "stacking_protocols"):
            logger.info("stacking_protocols already seeded, skipping")
            return

        import json
        protocols = [
            ("recovery-stack", "Recovery Stack", "injury_recovery",
             "Gold standard tissue repair protocol combining complementary healing mechanisms",
             json.dumps([
                 {"peptide": "BPC-157", "dose": "250mcg", "frequency": "2x daily", "route": "subcutaneous", "timing": "AM + PM, near injury site"},
                 {"peptide": "TB-500", "dose": "2-5mg", "frequency": "2x weekly (loading), then weekly", "route": "subcutaneous", "timing": "Any time"},
                 {"peptide": "GHK-Cu", "dose": "1mg", "frequency": "daily", "route": "subcutaneous or topical", "timing": "AM"},
             ]),
             "BPC-157 near injury site. TB-500 anywhere (systemic). GHK-Cu for skin/collagen component. Loading phase 4 weeks, then reduce TB-500 to weekly.",
             "6-8 weeks", ["Active cancer", "Pregnancy"], "B", "Dr. Seeds / Sports medicine",
             "Most widely used recovery stack. BPC + TB is the foundation; GHK-Cu adds collagen repair. FDA note: BPC-157 is Category 2 in US."),

            ("gh-optimization", "GH Optimization Stack", "anti_aging",
             "Clinically proven GH secretagogue combination for anti-aging, body composition, and recovery",
             json.dumps([
                 {"peptide": "CJC-1295 (no DAC)", "dose": "100mcg", "frequency": "daily", "route": "subcutaneous", "timing": "30 min before bed, empty stomach"},
                 {"peptide": "Ipamorelin", "dose": "100-200mcg", "frequency": "daily", "route": "subcutaneous", "timing": "30 min before bed, empty stomach"},
             ]),
             "Inject together before bed on empty stomach (2+ hours after eating). Fasted state maximizes GH pulse. Do not combine with HGH.",
             "12 weeks on, 4 weeks off", ["Active cancer", "Pituitary disorders"], "A", "Anti-aging medicine consensus",
             "Most popular GH stack. Both removed from FDA Category 2 (Sept 2024) — legal to compound. WADA prohibited."),

            ("cognitive-stack", "Cognitive Enhancement Stack", "cognition",
             "Nootropic peptide combination for focus, memory, and neuroprotection",
             json.dumps([
                 {"peptide": "Semax", "dose": "200-600mcg", "frequency": "1-2x daily", "route": "intranasal", "timing": "Morning and early afternoon"},
                 {"peptide": "Selank", "dose": "250-500mcg", "frequency": "1-2x daily", "route": "intranasal", "timing": "As needed for anxiety, or PM"},
                 {"peptide": "NAD+", "dose": "50-100mg SubQ or 250mg IV weekly", "frequency": "daily SubQ or weekly IV", "route": "subcutaneous or IV", "timing": "Morning"},
             ]),
             "Semax for focus (AM). Selank for calm/anxiety (PM or as needed). NAD+ for cellular energy foundation. Can use Semax and Selank same day.",
             "2-4 week cycles, 1 week off", [], "B", "Russian pharmaceutical practice + biohacking",
             "Classic nootropic stack. Semax + Selank are approved pharmaceuticals in Russia. Add DSIP at night if sleep is a concern."),

            ("longevity-stack", "Longevity Protocol", "longevity",
             "Multi-pathway anti-aging protocol targeting telomeres, mitochondria, NAD+, and cellular senescence",
             json.dumps([
                 {"peptide": "Epithalon", "dose": "5-10mg", "frequency": "daily for 10-20 days", "route": "subcutaneous", "timing": "Morning, cycled 2-3x/year"},
                 {"peptide": "NAD+", "dose": "50-100mg SubQ daily or 250-500mg IV weekly", "frequency": "daily or weekly", "route": "subcutaneous or IV", "timing": "Morning"},
                 {"peptide": "MOTS-c", "dose": "5-10mg", "frequency": "3-5x per week", "route": "subcutaneous", "timing": "Exercise days, pre-workout"},
                 {"peptide": "Thymosin Alpha-1", "dose": "1.6mg", "frequency": "2x weekly", "route": "subcutaneous", "timing": "Any time"},
             ]),
             "Epithalon in short cycles (10-20 days, 2-3x/year). NAD+ and MOTS-c ongoing. TA-1 for immune longevity. Foundation: sleep, exercise, nutrition must be dialed in.",
             "Ongoing with cycling (Epithalon cycled, others continuous)", ["Active cancer", "Autoimmune conditions"], "C", "Longevity medicine",
             "Advanced protocol. Requires baseline bloodwork and monitoring. Each compound targets different aging pathway."),

            ("fat-loss-stack", "Fat Loss Protocol", "fat_loss",
             "Peptide combination targeting fat metabolism through multiple mechanisms",
             json.dumps([
                 {"peptide": "AOD-9604", "dose": "250-500mcg", "frequency": "daily", "route": "subcutaneous", "timing": "Morning, fasted, in abdominal fat"},
                 {"peptide": "CJC-1295 + Ipamorelin", "dose": "100mcg each", "frequency": "daily", "route": "subcutaneous", "timing": "Before bed, empty stomach"},
                 {"peptide": "MOTS-c", "dose": "5mg", "frequency": "3-5x/week", "route": "subcutaneous", "timing": "Exercise days"},
             ]),
             "AOD-9604 fasted AM targets lipolysis. GH secretagogues at bedtime support metabolism. MOTS-c on training days for metabolic activation. Maintain caloric deficit and high protein.",
             "12-16 weeks", ["Cancer history", "Diabetes (monitor closely)"], "B", "Body composition protocols",
             "Effective fat loss stack without GLP-1 side effects. Requires training and nutrition dialed in. Not a shortcut."),

            ("immune-stack", "Immune Fortification Stack", "immune_support",
             "Comprehensive immune optimization for chronic infections, post-illness recovery, or immune support",
             json.dumps([
                 {"peptide": "Thymosin Alpha-1", "dose": "1.6mg", "frequency": "2x weekly", "route": "subcutaneous", "timing": "Mon/Thu"},
                 {"peptide": "LL-37", "dose": "50-100mcg", "frequency": "daily", "route": "subcutaneous", "timing": "Daily for 2-4 weeks"},
                 {"peptide": "KPV", "dose": "200-500mcg", "frequency": "1-2x daily", "route": "oral or subcutaneous", "timing": "With meals if oral"},
                 {"peptide": "Glutathione", "dose": "200-400mg", "frequency": "2-3x weekly", "route": "subcutaneous", "timing": "Any time"},
             ]),
             "TA-1 for adaptive immunity (ongoing). LL-37 for acute antimicrobial (short courses). KPV for anti-inflammatory. Glutathione for antioxidant support.",
             "4-12 weeks depending on indication", ["Autoimmune conditions (TA-1 may exacerbate)"], "B", "Immune peptide protocols",
             "Layered immune support. TA-1 is the foundation (approved in 30+ countries). Add others based on specific needs."),

            ("gut-healing-stack", "Gut Healing Protocol", "gut_health",
             "Targeted protocol for gut repair, IBD support, and microbiome restoration",
             json.dumps([
                 {"peptide": "BPC-157", "dose": "500mcg", "frequency": "daily", "route": "oral", "timing": "Empty stomach, AM"},
                 {"peptide": "KPV", "dose": "200-500mcg", "frequency": "1-2x daily", "route": "oral", "timing": "With meals"},
                 {"peptide": "Glutathione", "dose": "200mg", "frequency": "daily", "route": "oral (liposomal)", "timing": "AM, empty stomach"},
             ]),
             "All oral for direct GI tract delivery. BPC-157 capsules for gut lining repair. KPV for inflammation. Glutathione for oxidative stress. Add probiotics and bone broth.",
             "4-8 weeks", ["Active GI malignancies"], "C", "GI peptide protocols",
             "Oral-only protocol for accessibility. FDA note: BPC-157 is Category 2 in US. KPV and glutathione are unregulated."),
        ]

        for slug, name, goal, desc, compounds, timing, dur, contras, ev, src, notes in protocols:
            cur.execute(
                """INSERT INTO stacking_protocols
                   (slug, name, goal, description, compounds, timing_notes, duration,
                    contraindications, evidence_level, source, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (slug) DO NOTHING""",
                (slug, name, goal, desc, compounds, timing, dur, contras, ev, src, notes),
            )
        logger.info(f"Seeded {len(protocols)} stacking protocols")


# ═══════════════════════════════════════════════════════════════════════
# SEED ALL V2
# ═══════════════════════════════════════════════════════════════════════

def seed_all_v2():
    """Run all v2 knowledge seeders. Safe to call on every startup."""
    try:
        seed_evidence_tiers()
        seed_regulatory_status()
        seed_new_peptides()
        seed_peptide_interactions()
        seed_stacking_protocols()
        logger.info("Knowledge base v2 seeding complete")
    except Exception as e:
        logger.error(f"Knowledge base v2 seeding error: {e}")
