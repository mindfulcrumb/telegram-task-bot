"""Seed knowledge v3 — Deep expert protocols from compiled research.

Covers gaps identified from the user's comprehensive peptide research document:
- Koniver NAD IV protocol
- Jay Campbell GLOW/KLOW protocols
- Epitalon + Thymalin 4.1x telomere data
- Hexarelin and Thymalin compounds
- AJA Cortes contrarian perspective
- Expert consensus framework (6 key figures)
- Category-specific deep protocols
"""
import logging

from bot.db.database import get_cursor
from bot.services.knowledge_service import add_knowledge_entry

logger = logging.getLogger(__name__)

_SENTINEL_SOURCE = "expert_research_v3"


def _already_seeded() -> bool:
    """Check if v3 seed data has already been loaded."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM knowledge_base WHERE source = %s LIMIT 1",
            (_SENTINEL_SOURCE,),
        )
        return cur.fetchone() is not None


# ─── Expert Knowledge Entries ────────────────────────────────────────

EXPERT_ENTRIES = [
    # ── Koniver Protocols ──
    {
        "category": "longevity",
        "topic": "peptides",
        "title": "Koniver NAD+ IV Protocol — Full Regenerative Approach",
        "content": (
            "Dr. Craig Koniver's NAD+ IV protocol is considered the gold standard for cellular energy restoration. "
            "The protocol uses 250mg NAD+ IV infusion paired with glutathione push, administered 2x/week during "
            "the loading phase (4-6 weeks), then tapering to 1x/week maintenance. The IV route bypasses first-pass "
            "metabolism, achieving near-100% bioavailability vs. 2-10% oral. Koniver layers this with subcutaneous "
            "NAD+ (50-100mg) on non-IV days for sustained levels. Key mechanism: NAD+ is the rate-limiting cofactor "
            "for sirtuins (SIRT1-7), PARP DNA repair enzymes, and CD38-mediated immune signaling. Declining NAD+ "
            "(~50% loss by age 60) is now considered a hallmark of aging alongside telomere shortening and epigenetic "
            "drift. Side effects during infusion: chest tightness, nausea, and flushing are common and dose-dependent — "
            "slower infusion rates (2-4 hours) reduce discomfort. Koniver recommends concurrent methylation support "
            "(methylfolate + methylcobalamin) since NAD+ synthesis consumes methyl donors. Cost: $500-1500/infusion "
            "depending on clinic. Evidence: multiple observational studies show improved mitochondrial function markers, "
            "but no large RCTs to date. Evidence level B — strong mechanistic basis, clinical adoption, limited RCT data."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Dr. Craig Koniver — Koniver Wellness protocols",
        "tags": ["koniver", "nad", "iv_therapy", "longevity", "mitochondria", "sirtuins"],
        "evidence_level": "B",
    },
    {
        "category": "longevity",
        "topic": "peptides",
        "title": "Koniver Performance Medicine — Full Stack Peptide Approach",
        "content": (
            "Dr. Craig Koniver (Koniver Wellness) pioneered the 'Performance Medicine' framework — using peptides, "
            "IV nutrients, and biomarker tracking as a unified system rather than isolated interventions. His approach: "
            "(1) Foundation layer: NAD+ IV 250mg 2x/week + glutathione, (2) Repair layer: BPC-157 500mcg subQ 2x/day "
            "for tissue healing + TB-500 2.5mg subQ 2x/week for systemic inflammation, (3) Growth layer: Ipamorelin "
            "200-300mcg + CJC-1295 100mcg subQ at bedtime for GH pulse optimization, (4) Longevity layer: Epitalon "
            "10mg IM for 10-day cycles twice yearly for telomere maintenance. Koniver emphasizes that peptides work "
            "synergistically — BPC-157's angiogenesis + TB-500's thymosin-mediated immune repair create a 'healing "
            "cascade' greater than either alone. He tracks progress via quarterly labs: IGF-1, inflammatory markers "
            "(hs-CRP, IL-6), telomere length (via RepeatDx), and metabolic panels. Key insight: Koniver treats peptide "
            "therapy as a 'systems upgrade' not a single-compound intervention. He's treated 5,000+ patients with this "
            "framework since 2015. Caution: requires medical supervision, comprehensive bloodwork baseline, and "
            "understanding of individual contraindications."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Dr. Craig Koniver — Performance Medicine framework",
        "tags": ["koniver", "peptides", "performance_medicine", "bpc-157", "tb-500", "ipamorelin", "nad"],
        "evidence_level": "B",
    },

    # ── Jay Campbell Protocols ──
    {
        "category": "longevity",
        "topic": "peptides",
        "title": "Jay Campbell GLOW Protocol — GHK-Cu 5:1:1 Ratio",
        "content": (
            "Jay Campbell's GLOW protocol (Growth-Longevity-Optimization-Wellness) centers on GHK-Cu at a specific "
            "5:1:1 dosing ratio. The protocol: GHK-Cu 1-2mg subQ daily as the anchor (5 parts), paired with "
            "BPC-157 200-500mcg (1 part) and TB-500 750mcg-2.5mg (1 part). The 5:1:1 ratio is Campbell's "
            "proprietary recommendation based on clinical observation across his patient cohort. GHK-Cu is a "
            "naturally occurring copper tripeptide (glycyl-L-histidyl-L-lysine) that declines with age — serum "
            "levels drop from 200ng/mL at age 20 to 80ng/mL by age 60. Mechanism: GHK-Cu activates genes involved "
            "in collagen synthesis (COL1A1, COL3A1), antioxidant defense (SOD, glutathione peroxidase), and stem "
            "cell recruitment. It also suppresses pro-inflammatory genes (IL-6, TNF-alpha) and activates DNA repair "
            "pathways. Campbell reports patients show improved skin elasticity, wound healing, and inflammatory "
            "markers within 4-6 weeks. For dermal benefits: topical GHK-Cu 0.01% cream applied to face/neck "
            "alongside subQ injections. Campbell emphasizes cycling: 6 weeks on, 2 weeks off. Evidence level B — "
            "GHK-Cu has 20+ published studies on wound healing and gene expression, but the specific GLOW ratio "
            "lacks formal clinical trials."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Jay Campbell — GLOW protocol, The TOT Bible",
        "tags": ["jay_campbell", "ghk-cu", "glow", "bpc-157", "tb-500", "skin", "collagen"],
        "evidence_level": "B",
    },
    {
        "category": "longevity",
        "topic": "peptides",
        "title": "Jay Campbell KLOW Protocol — KPV Anti-Inflammatory Stack",
        "content": (
            "Jay Campbell's KLOW (KPV-Longevity-Optimization-Wellness) protocol focuses on gut healing and "
            "systemic inflammation reduction. The centerpiece is KPV (Lysine-Proline-Valine), a tripeptide derived "
            "from alpha-MSH with potent anti-inflammatory properties. Protocol: KPV 500mcg-1mg subQ or oral daily, "
            "combined with BPC-157 250mcg subQ for gut mucosal healing. KPV works through PepT1 transporter-mediated "
            "intestinal absorption and directly inhibits NF-kB inflammatory signaling in colonocytes. In preclinical "
            "models, KPV reduced colitis severity by 60-70% and promoted mucosal barrier integrity. Campbell "
            "recommends KLOW specifically for: inflammatory bowel conditions, leaky gut syndrome, post-antibiotic "
            "gut restoration, and autoimmune-associated GI inflammation. Oral administration is effective because "
            "KPV is absorbed via intestinal PepT1 transporters, reaching colonic epithelial cells directly. "
            "Campbell cycles KLOW: 8 weeks on, 4 weeks off. Pair with comprehensive probiotic protocol and "
            "elimination of inflammatory triggers. Evidence level B-C — KPV has strong preclinical data but "
            "limited human clinical trials. Campbell's recommendations are based on clinical observation."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Jay Campbell — KLOW protocol, peptide optimization",
        "tags": ["jay_campbell", "kpv", "gut_health", "inflammation", "bpc-157", "anti-inflammatory"],
        "evidence_level": "B",
    },
    {
        "category": "hormones",
        "topic": "hormones",
        "title": "Jay Campbell — Testosterone Optimization Therapy (TOT)",
        "content": (
            "Jay Campbell authored 'The TOT Bible' and is considered the leading patient advocate for testosterone "
            "optimization therapy. His framework: (1) Baseline: total T, free T, SHBG, estradiol (sensitive), DHT, "
            "PSA, CBC, CMP, lipid panel, thyroid panel. (2) Optimal ranges — not 'normal': total T 700-1100 ng/dL, "
            "free T 20-30 pg/mL, E2 20-30 pg/mL for men. (3) Preferred protocol: testosterone cypionate 100-200mg "
            "IM or subQ weekly, split into 2-3 injections for stable levels. (4) Adjuncts: HCG 250-500 IU 2-3x/week "
            "for fertility preservation and intratesticular testosterone, anastrozole 0.25-0.5mg 2x/week ONLY if E2 "
            "exceeds 30-35 pg/mL (Campbell is against routine AI use). (5) Key insight: Campbell emphasizes body "
            "composition optimization BEFORE starting TRT — insulin resistance and excess body fat increase "
            "aromatase activity, making T management harder. His peptide layering: add Ipamorelin/CJC-1295 for "
            "GH optimization on top of testosterone base. He advocates for patient autonomy and informed consent, "
            "with quarterly bloodwork monitoring. Evidence: testosterone replacement has extensive RCT support for "
            "hypogonadal men; optimal range targeting is more practice-based."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Jay Campbell — The TOT Bible, testosterone optimization",
        "tags": ["jay_campbell", "testosterone", "trt", "hormones", "hcg", "optimization"],
        "evidence_level": "A",
    },

    # ── Epitalon + Thymalin Research ──
    {
        "category": "longevity",
        "topic": "longevity",
        "title": "Epitalon + Thymalin Combination — 4.1x Mortality Reduction Study",
        "content": (
            "The landmark Khavinson study on Epitalon (epithalamin/Ala-Glu-Asp-Gly) combined with Thymalin "
            "(thymic peptide extract) demonstrated a 4.1x reduction in mortality over a 6-year follow-up in "
            "elderly patients (60-80 years). Study design: 266 participants in a controlled trial, treatment group "
            "received Epitalon 10mg IM daily for 10 days + Thymalin 10mg IM daily for 10 days, administered "
            "annually. Results: treatment group mortality was 4.1 times lower than the untreated control group. "
            "Mechanisms: Epitalon stimulates telomerase activity in somatic cells, specifically increasing TERT "
            "gene expression. Thymalin restores thymic function and T-cell maturation, countering age-related "
            "immunosenescence (thymic involution). The combination addresses two key aging hallmarks simultaneously: "
            "telomere attrition and immune system decline. In vitro, Epitalon increased telomere length by 33% in "
            "human fetal fibroblasts and extended their replicative lifespan by 44%. Thymalin restored the "
            "CD4/CD8 ratio toward youthful levels in elderly subjects. Dosing protocol: 10-day cycles of each, "
            "either sequentially or concurrently, repeated every 6-12 months. Both peptides have been used "
            "clinically in Russia since the 1980s with extensive safety data. Limitations: research primarily "
            "from Khavinson's group, not independently replicated in Western RCTs. Evidence level B+ — compelling "
            "data but single-group research."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Khavinson et al. — Epitalon/Thymalin longevity study",
        "tags": ["epitalon", "thymalin", "telomere", "longevity", "mortality", "khavinson", "aging"],
        "evidence_level": "B",
    },

    # ── Hexarelin ──
    {
        "category": "peptides",
        "topic": "peptides",
        "title": "Hexarelin — GHSR Agonist with Unique Cardiac Benefits",
        "content": (
            "Hexarelin (His-D-2-methyl-Trp-Ala-Trp-D-Phe-Lys-NH2) is a synthetic growth hormone secretagogue "
            "receptor (GHSR/ghrelin receptor) agonist with distinctive cardiac protective properties not shared "
            "by other GH peptides. Unlike Ipamorelin (which is GH-specific), Hexarelin activates cardiac GHSR "
            "receptors independent of GH release, providing direct cardioprotective effects. Cardiac benefits: "
            "improved left ventricular ejection fraction in heart failure patients (15-20% improvement in studies), "
            "reduced cardiac fibrosis, enhanced coronary blood flow, and protection against ischemia-reperfusion "
            "injury. Dosing: 1-2mcg/kg subQ, 2-3x daily. Peak GH release occurs 15-30 minutes post-injection. "
            "Key difference from other GH peptides: Hexarelin causes desensitization — GH release diminishes "
            "after 4-8 weeks of continuous use, requiring cycling (4 weeks on, 4 weeks off). It also raises "
            "cortisol and prolactin more than Ipamorelin, making it less ideal for long-term GH optimization "
            "but superior for cardiac applications. Clinical studies: Bisi et al. (1999) showed sustained cardiac "
            "improvements in heart failure patients; Broglio et al. (2002) demonstrated GH-independent cardiac "
            "effects. Best use case: short-term cardiac rehabilitation or cyclical GH boosting. Not recommended "
            "as a first-line GH peptide due to desensitization and cortisol effects."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Hexarelin research — cardiac GHSR agonist literature",
        "tags": ["hexarelin", "ghsr", "cardiac", "growth_hormone", "heart", "peptides"],
        "evidence_level": "B",
    },

    # ── Thymalin ──
    {
        "category": "longevity",
        "topic": "peptides",
        "title": "Thymalin — Thymic Peptide for Immune System Rejuvenation",
        "content": (
            "Thymalin is a peptide complex extracted from calf thymus gland, developed by Khavinson's group at "
            "the St. Petersburg Institute of Bioregulation and Gerontology. It addresses thymic involution — the "
            "progressive shrinkage of the thymus gland that begins after puberty and is nearly complete by age 65, "
            "resulting in severely compromised T-cell production and adaptive immunity. Mechanism: Thymalin contains "
            "bioregulatory peptides that restore thymic epithelial cell function, promote thymocyte maturation, and "
            "normalize the CD4/CD8 T-cell ratio. It also modulates cytokine production (reduces IL-6, increases "
            "IL-2) and enhances natural killer cell activity. Protocol: 10mg intramuscular daily for 10 consecutive "
            "days, repeated every 6-12 months. Clinical data: In a 6-year study of 266 elderly patients, Thymalin "
            "combined with Epitalon reduced mortality by 4.1x compared to untreated controls. Thymalin alone "
            "improved immune function markers: increased T-cell counts by 30-50%, normalized CD4/CD8 ratio from "
            "inverted (<1.0) to normal (1.5-2.0), and reduced incidence of respiratory infections by 40-60% in "
            "elderly subjects. Side effects: generally well-tolerated; mild injection site reactions. Has been used "
            "clinically in Russia since 1982 with extensive safety data across thousands of patients. Limitation: "
            "as a thymus extract, batch consistency may vary. Synthetic alternatives (thymosin alpha-1, "
            "thymalin-derived specific peptides) are being developed for better standardization."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Thymalin research — Khavinson Institute of Bioregulation",
        "tags": ["thymalin", "thymus", "immune", "longevity", "t-cells", "aging", "khavinson"],
        "evidence_level": "B",
    },

    # ── AJA Cortes Perspective ──
    {
        "category": "longevity",
        "topic": "general",
        "title": "AJA Cortes — Contrarian Perspective on Peptide Risk Assessment",
        "content": (
            "AJA Cortes represents the critical/contrarian voice in the peptide optimization space, emphasizing "
            "risk assessment and foundational health before peptide use. Key positions: (1) 'Master the basics "
            "first' — most people using peptides haven't optimized sleep (7-9 hours), nutrition (1g/lb protein, "
            "micronutrient sufficiency), exercise (3-5x/week strength + 150min/week Zone 2), and stress management. "
            "(2) Peptides amplify your baseline — if your foundation is broken, peptides won't fix it. (3) The "
            "'peptide culture' creates premature optimization — 25-year-olds using GH peptides when natural GH "
            "pulsatility is still intact. (4) Risk-reward calculation: BPC-157 and TB-500 for injury recovery "
            "have favorable risk profiles. GH peptides in healthy young adults are harder to justify. Semaglutide "
            "for non-obese individuals carries potential risks (muscle loss, gallbladder issues, unknown long-term "
            "pancreatic effects) that may not be worth cosmetic weight loss. (5) Regulatory reality: most peptides "
            "are research chemicals sold by gray-market vendors with no quality assurance. Third-party testing "
            "(CoA from independent labs) should be mandatory before injecting anything. (6) Long-term unknowns: "
            "we have 2-5 years of widespread peptide use data, not 20-30 years. Iatrogenic harm from premature "
            "adoption is a real risk. Cortes's framework is valuable as a counterbalance — 'just because you CAN "
            "doesn't mean you SHOULD.' He recommends exhausting natural optimization before any peptide protocol."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "AJA Cortes — contrarian peptide risk framework",
        "tags": ["aja_cortes", "risk_assessment", "foundational_health", "peptides", "contrarian"],
        "evidence_level": "C",
    },

    # ── Expert Consensus Framework ──
    {
        "category": "longevity",
        "topic": "general",
        "title": "Expert Consensus — 6-Figure Authority Map for Peptide Knowledge",
        "content": (
            "The peptide/longevity space is shaped by six key thought leaders, each with distinct expertise and "
            "trust tiers. Understanding who to follow for what prevents misinformation: "
            "(1) DR. ANDREW HUBERMAN (Stanford neuroscience) — Trust for: light exposure, dopamine, sleep, "
            "supplement mechanisms. His strength is translating peer-reviewed neuroscience into protocols. Weakness: "
            "sometimes oversimplifies dose-response for broad audiences. "
            "(2) DR. PETER ATTIA (Medicine 2.0/3.0) — Trust for: exercise physiology (Zone 2, VO2max), metabolic "
            "health, cancer screening, cardiovascular risk. Most rigorous evidence-based framework. Does NOT "
            "typically endorse peptides publicly. "
            "(3) DR. DAVID SINCLAIR (Harvard genetics) — Trust for: aging biology, NAD+, sirtuins, epigenetic "
            "reprogramming theory. Caveat: commercial interests (Tally Health, supplement line) create conflicts. "
            "Some claims (resveratrol, NMN magnitude of effect) are debated. "
            "(4) DR. CRAIG KONIVER (Koniver Wellness) — Trust for: practical peptide stacking, NAD+ IV protocols, "
            "real-world patient outcomes over 5,000+ cases. Most clinical peptide experience. "
            "(5) JAY CAMPBELL (author/advocate) — Trust for: testosterone optimization, peptide protocols (GLOW, "
            "KLOW), patient advocacy. Not a physician — relies on clinical advisors. Commercial interests in "
            "supplements and programs. "
            "(6) AJA CORTES (fitness/contrarian) — Trust for: risk-benefit analysis, foundational health emphasis, "
            "skepticism of premature optimization. Valuable counterbalance but can be overly dismissive."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Expert consensus — authority map compiled from multiple sources",
        "tags": ["experts", "consensus", "huberman", "attia", "sinclair", "koniver", "jay_campbell", "aja_cortes"],
        "evidence_level": "B",
    },

    # ── Category-Specific Deep Protocols ──
    {
        "category": "longevity",
        "topic": "peptides",
        "title": "Tissue Repair Peptides — BPC-157 + TB-500 Synergy Protocol",
        "content": (
            "BPC-157 and TB-500 are the most evidence-supported peptides for tissue repair and represent the "
            "entry point for most peptide protocols. Synergy mechanism: BPC-157 (Body Protection Compound, "
            "a gastric pentadecapeptide) promotes angiogenesis (new blood vessel formation via VEGF upregulation), "
            "nitric oxide modulation, and tendon/ligament fibroblast activation. TB-500 (Thymosin Beta-4 fragment) "
            "promotes cell migration, reduces inflammation via actin polymerization modulation, and activates "
            "satellite cells for muscle repair. Together they address repair from both vascular (BPC) and cellular "
            "(TB-500) angles. Protocol: BPC-157 250-500mcg subQ twice daily, injected near injury site when "
            "possible + TB-500 2.0-2.5mg subQ twice weekly (systemic). Duration: 4-8 weeks for acute injuries, "
            "8-12 weeks for chronic conditions (tendinopathy, post-surgical healing). Cycling: 6-8 weeks on, "
            "4 weeks off. Both have excellent safety profiles in preclinical studies. BPC-157 has 100+ published "
            "studies demonstrating efficacy for gut healing, tendon repair, neuroprotection, and organ protection. "
            "TB-500 has strong equine veterinary data and growing human clinical evidence. Key consideration: "
            "source quality is critical — third-party testing (HPLC purity >98%, endotoxin testing) from "
            "reputable vendors. FDA status: both are unregulated research chemicals, not approved drugs."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "BPC-157 + TB-500 — compiled clinical and research literature",
        "tags": ["bpc-157", "tb-500", "tissue_repair", "healing", "angiogenesis", "peptides"],
        "evidence_level": "B",
    },
    {
        "category": "longevity",
        "topic": "peptides",
        "title": "GH Optimization Peptides — Ipamorelin/CJC-1295 Protocol Deep Dive",
        "content": (
            "Ipamorelin + CJC-1295 (no DAC) is the gold standard GH secretagogue stack, preferred over direct "
            "GH administration for its safety profile and physiological pulsatility. Mechanism: CJC-1295 (a GHRH "
            "analog) stimulates the pituitary to produce GH, while Ipamorelin (a ghrelin receptor agonist) "
            "amplifies the pulse magnitude. Together they create a synergistic GH pulse 3-5x greater than either "
            "alone. Key advantage over exogenous GH: preserves the pulsatile pattern (GH should spike, not remain "
            "elevated), does not suppress endogenous production, and Ipamorelin specifically does NOT raise "
            "cortisol, prolactin, or ghrelin (unlike GHRP-6 or Hexarelin). Protocol: Ipamorelin 200-300mcg + "
            "CJC-1295 (no DAC) 100mcg subQ at bedtime, injected on an empty stomach (2+ hours post-meal). "
            "GH release peaks 15-30 min post-injection, augmenting the natural nocturnal GH surge. Some "
            "practitioners add a morning dose (Ipamorelin 100-200mcg only) for daytime benefits. Cycle: 8-12 "
            "weeks on, 4 weeks off. Expected outcomes: improved body composition (reduced visceral fat, maintained "
            "lean mass), enhanced recovery, deeper sleep, improved skin elasticity. Timeline: noticeable effects "
            "at 4-6 weeks, peak results at 3-6 months. Lab monitoring: IGF-1 (target 200-300 ng/dL), fasting "
            "glucose/insulin (GH can impair insulin sensitivity), HbA1c quarterly. IMPORTANT: 'CJC-1295 no DAC' "
            "is NOT the same as 'CJC-1295 DAC' — the DAC version has a drug affinity complex that creates "
            "sustained (non-pulsatile) GH elevation, which is less desirable."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Ipamorelin/CJC-1295 — compiled clinical protocols",
        "tags": ["ipamorelin", "cjc-1295", "growth_hormone", "peptides", "gh_optimization", "sleep"],
        "evidence_level": "B",
    },
    {
        "category": "longevity",
        "topic": "weight_management",
        "title": "GLP-1 Agonists — Semaglutide, Tirzepatide, Retatrutide Comparison",
        "content": (
            "GLP-1 receptor agonists represent the most significant advance in weight management and metabolic "
            "health in decades, with implications beyond obesity. Comparison: "
            "SEMAGLUTIDE (Ozempic/Wegovy): GLP-1 agonist only. Weight loss: 15-17% body weight in STEP trials. "
            "Dose: 0.25mg weekly escalating to 2.4mg over 16 weeks. Most clinical data and longest track record. "
            "SELECT trial showed 20% cardiovascular event reduction independent of weight loss. "
            "TIRZEPATIDE (Mounjaro/Zepbound): Dual GIP/GLP-1 agonist. Weight loss: 20-25% in SURMOUNT trials — "
            "superior to semaglutide. Dose: 2.5mg weekly escalating to 15mg. The GIP component may provide "
            "additive metabolic benefits and potentially better tolerability. "
            "RETATRUTIDE: Triple agonist (GLP-1/GIP/glucagon). Phase 2: up to 24% weight loss at 48 weeks. "
            "The glucagon component adds thermogenesis and hepatic fat reduction. Phase 3 trials ongoing. "
            "Key concerns across all: (1) Muscle loss — 25-40% of weight lost is lean mass without resistance "
            "training + 1.2-1.6g/kg/day protein. (2) 'Ozempic face' — facial fat loss creating gaunt appearance. "
            "(3) GI side effects: nausea, vomiting, diarrhea (dose-dependent, improves with time). "
            "(4) Weight regain: STEP-1 extension showed 2/3 of weight regained within 1 year of stopping. "
            "(5) Unknown long-term: thyroid C-cell tumors (rodent signal, unconfirmed in humans), pancreatitis, "
            "gallbladder disease. Expert consensus: strong for T2 diabetes and BMI>30; more nuanced for "
            "BMI 25-30 'cosmetic' use due to muscle loss and rebound risk."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "GLP-1 agonist comparison — STEP/SURMOUNT/Phase 2 trial data",
        "tags": ["semaglutide", "tirzepatide", "retatrutide", "glp-1", "weight_management", "metabolic"],
        "evidence_level": "A",
    },
    {
        "category": "longevity",
        "topic": "longevity",
        "title": "Regulatory Reality — FDA, WADA, and Peptide Legal Status 2024-2026",
        "content": (
            "Understanding the regulatory landscape is critical for safe peptide use. Current status: "
            "FDA-APPROVED as drugs: Semaglutide (Ozempic/Wegovy), Tirzepatide (Mounjaro/Zepbound), Tesamorelin "
            "(Egrifta for HIV lipodystrophy), Thymosin alpha-1 (Zadaxin, approved outside US). These are legal "
            "with prescription and regulated for quality. "
            "FDA 'CATEGORY 2' BULK COMPOUNDING BAN (2023-2024): The FDA moved to restrict compounding pharmacies "
            "from making copies of GLP-1 agonists during the 'shortage era,' affecting patient access. BPC-157, "
            "TB-500, and many research peptides are not FDA-approved drugs and exist in regulatory gray area — "
            "legal to purchase as 'research chemicals' but not for human use officially. "
            "WADA PROHIBITED: All GH secretagogues (Ipamorelin, CJC-1295, GHRP-2/6, Hexarelin), GH itself, "
            "SARMs, and peptide hormones are on the WADA prohibited list. Athletes risk multi-year bans. "
            "GHK-Cu, BPC-157, and KPV are NOT currently on the WADA list. "
            "QUALITY CONCERN: The compounding pharmacy landscape ranges from USP 800-compliant facilities to "
            "unregulated overseas labs. Minimum quality markers: (1) Certificate of Analysis (CoA) with HPLC "
            "purity >98%, (2) Endotoxin testing, (3) Mass spectrometry confirmation, (4) Third-party lab "
            "verification (not just vendor-supplied CoA). Reputable US compounding pharmacies: Empower Pharmacy, "
            "Tailor Made Compounding, Hallandale Pharmacy. Gray-market vendors vary wildly in quality."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Regulatory landscape — FDA, WADA, compounding pharmacy status",
        "tags": ["regulatory", "fda", "wada", "legal", "compounding", "quality", "peptides"],
        "evidence_level": "A",
    },
    {
        "category": "neuroscience",
        "topic": "neuroscience",
        "title": "Nootropic Peptides — Selank, Semax, and DSIP Protocols",
        "content": (
            "Three peptides dominate the nootropic/neuromodulation space, each with distinct mechanisms: "
            "SELANK (Thr-Lys-Pro-Arg-Pro-Gly-Pro): A synthetic tuftsin analog developed at the Russian Academy "
            "of Sciences. Mechanism: modulates GABA-A receptor allosterically (similar to benzodiazepines but "
            "without addiction/tolerance), increases BDNF expression, and stabilizes enkephalin levels. Dose: "
            "250-500mcg intranasal 2-3x/day. Effects: anxiolysis without sedation, improved focus, enhanced "
            "memory consolidation. Onset: 10-15 minutes intranasal. Russian clinical approval for anxiety "
            "disorders. Evidence level B. "
            "SEMAX (Met-Glu-His-Phe-Pro-Gly-Pro): ACTH(4-7) analog, also Russian-developed. Mechanism: "
            "increases BDNF and NGF (nerve growth factor), enhances dopaminergic and serotonergic transmission, "
            "neuroprotective against oxidative stress. Dose: 200-600mcg intranasal 2-3x/day. Primary use: "
            "cognitive enhancement, stroke recovery, optic nerve disease. Russian-approved for cognitive disorders "
            "and stroke rehabilitation. Evidence level B. "
            "DSIP (Delta Sleep-Inducing Peptide): A nonapeptide that promotes delta wave (deep) sleep. Dose: "
            "100-200mcg subQ or intranasal before bed. Mechanism: modulates GABAergic and glutamatergic "
            "neurotransmission, reduces cortisol, normalizes disrupted circadian signaling. Best for: shift "
            "workers, jet lag recovery, stress-induced insomnia. NOT a sedative — promotes natural sleep "
            "architecture. Evidence level C — more preclinical than clinical data."
        ),
        "source": _SENTINEL_SOURCE,
        "source_episode": "Nootropic peptides — Selank, Semax, DSIP research compilation",
        "tags": ["selank", "semax", "dsip", "nootropic", "neuroscience", "sleep", "anxiety", "cognitive"],
        "evidence_level": "B",
    },
]


def seed_expert_research():
    """Seed deep expert protocol entries from compiled research."""
    if _already_seeded():
        logger.info("Expert research v3 already seeded, skipping")
        return

    count = 0
    for entry in EXPERT_ENTRIES:
        try:
            add_knowledge_entry(
                category=entry["category"],
                topic=entry["topic"],
                title=entry["title"],
                content=entry["content"],
                source=entry["source"],
                source_episode=entry["source_episode"],
                tags=entry["tags"],
                evidence_level=entry["evidence_level"],
            )
            count += 1
        except Exception as e:
            logger.error(f"Failed to seed '{entry['title']}': {e}")

    logger.info(f"Expert research v3 seeded: {count} entries")


def seed_all_v3():
    """Run all v3 seed operations."""
    seed_expert_research()
