/**
 * demo_crm.js — Live Platform Demo
 *
 * Lets partners submit a fake CRM case directly into the real CostPilot
 * routing pipeline and see the governance decision in real time.
 */

// ── State ─────────────────────────────────────────────────────────────────────

let selectedPlatform    = "salesforce";
let voiceGuardEnabled   = false;
let activeScenario      = null;
let lastAuditEventId    = null;

// ── Platform selector ─────────────────────────────────────────────────────────

function selectPlatform(platform, defaultAgent) {
  selectedPlatform = platform;
  document.querySelectorAll(".plat-tile").forEach(el => el.classList.remove("selected"));
  document.getElementById("plat-" + platform).classList.add("selected");
  // Update agent name only if it still matches a default
  const defaults = ["SF-CaseBot","SN-IncidentBot","HS-TicketBot","ZD-TicketBot","D365-CaseBot","CostPilot-Bot"];
  const current  = document.getElementById("agentName").value.trim();
  if (!current || defaults.includes(current)) {
    document.getElementById("agentName").value = defaultAgent;
  }
}

// ── Fixed scenario presets (buttons) ─────────────────────────────────────────

const SCENARIOS = {
  routine: {
    subject: "Password reset request",
    body:    "Hi, I need to reset my account password. I forgot it when I came back from vacation. Can you send a reset link to my email? Thanks.",
    dept:    "Support",
    vg:      false,
  },
  complex: {
    subject: "Critical outage — end-of-month reporting blocked",
    body:    `Our entire analytics dashboard has been inaccessible since Monday morning. The error message reads "Service Unavailable 503." This is a critical incident blocking our end-of-month financial reporting and compliance audit submission. We need a root cause analysis and a remediation timeline immediately. Our Operations team has already escalated internally. This is impacting 47 users across three departments. We require a full incident report for our regulatory review board.`,
    dept:    "Operations",
    vg:      false,
  },
  compliance: {
    subject: "GDPR data deletion request — urgent regulatory deadline",
    body:    `We have received a formal GDPR right-to-erasure request from a customer in the EU. The request was submitted 28 days ago and we are approaching the 30-day regulatory deadline. We need to confirm that all personal data for this individual has been purged from our systems, including backups. Our legal team and compliance officer are on this thread. Failure to comply would constitute a breach of GDPR Article 17 and could result in regulatory action. Please confirm the deletion protocol and provide written confirmation for our audit trail.`,
    dept:    "Operations",
    vg:      false,
  },
  blocked: {
    subject: "Payment issue — customer provided card details",
    body:    `The customer called in and provided the following to verify their account: their credit card number ending in the full 16 digits, along with the CVV and bank account routing number. They are asking us to process a refund. I have captured all the details here for the case record.`,
    dept:    "Support",
    vg:      false,
  },
  voice: {
    subject: "Post-call transcript — customer verification",
    body:    `Hey thanks for calling in, can you verify your identity for me? Sure, my name is James Williams and my social security number is three four five, uh let me check, two two, eight eight seven one. And my date of birth is March fifteenth nineteen eighty two. And my credit card number is four five three two, zero one five seven, zero one one nine, eight four eight four. Great, I've got that on file. How can I help you today?`,
    dept:    "Support",
    vg:      true,
  },
};

function loadScenario(name) {
  const s = SCENARIOS[name];
  if (!s) return;
  activeScenario = name;

  document.querySelectorAll(".scenario-btn").forEach(el => el.classList.remove("active"));
  const btns = document.querySelectorAll(".scenario-btn");
  const labels = ["routine","complex","compliance","blocked","voice"];
  const idx = labels.indexOf(name);
  if (idx >= 0) btns[idx].classList.add("active");

  document.getElementById("caseSubject").value = s.subject;
  document.getElementById("caseBody").value    = s.body;
  document.getElementById("deptSelect").value  = s.dept;

  // Voice Guard toggle
  const vgRow = document.getElementById("vgToggleRow");
  if (name === "voice") {
    vgRow.style.display = "flex";
    setVoiceGuard(true);
  } else {
    vgRow.style.display = "none";
    setVoiceGuard(false);
  }

  showWaiting();
}

// ── 60-case generated library ─────────────────────────────────────────────────
// Each case is hand-crafted with realistic detail.
// Tags: routine | complex | escalate | block | voice
// CostPilot expected outcomes noted in comments for reference.

const CASE_LIBRARY = [

  // ── ROUTINE — fast, cheap, Scout tier ────────────────────────────────────────

  { tag:"routine", dept:"Support",
    subject: "Can't log into my account",
    body: "Hi there, I'm getting an error when I try to sign in. It just says 'invalid credentials' but I haven't changed my password. Can you help me reset it or check if something is wrong on your end? Thanks." },

  { tag:"routine", dept:"Support",
    subject: "How do I export my invoices?",
    body: "I need to download all my invoices from the last 6 months for my accountant. I can see them in the billing tab but I can't find an export button anywhere. Is there a way to bulk download them as PDFs?" },

  { tag:"routine", dept:"Support",
    subject: "Update my billing address",
    body: "I recently moved and need to update the billing address on my account. The current one is outdated and my credit card company is flagging the mismatch. Can you walk me through how to update it?" },

  { tag:"routine", dept:"Sales",
    subject: "Request for product demo",
    body: "Hello, our team is evaluating platforms for our upcoming Q3 deployment. We have about 15 users who would need access. Could someone schedule a 30-minute walkthrough of the core features? We're particularly interested in the reporting and integration capabilities." },

  { tag:"routine", dept:"Support",
    subject: "How many users can I add to my plan?",
    body: "We just upgraded to the Business plan last week and I want to know how many seats we have available. I need to add 3 new team members but I want to make sure we have room before inviting them." },

  { tag:"routine", dept:"Marketing",
    subject: "Unsubscribe from marketing emails",
    body: "I'm receiving your weekly newsletter at two different email addresses. I'd like to unsubscribe the older one — marketing@oldcompany.com — but keep receiving emails at my current address. Can you remove just the one?" },

  { tag:"routine", dept:"Support",
    subject: "App is running slowly today",
    body: "The dashboard has been loading really slowly this afternoon — pages are taking about 8-10 seconds to load. Is there a known issue or scheduled maintenance happening? Everything was fine this morning." },

  { tag:"routine", dept:"Sales",
    subject: "Pricing question for annual plan",
    body: "We're currently on a monthly plan and considering switching to annual billing to get the discount. Can you tell me what the annual rate would be for our current 8-user Business plan, and whether we'd be billed immediately or at our next renewal?" },

  { tag:"routine", dept:"Support",
    subject: "Two-factor authentication not working",
    body: "I turned on 2FA last week and now I'm not receiving the text message codes when I try to log in. I've double checked the phone number on my account and it looks correct. Is there a backup method I can use to get back in?" },

  { tag:"routine", dept:"Operations",
    subject: "Need to change team admin",
    body: "Our previous IT admin, David Torres, left the company last month. I need to transfer admin rights to myself before his account is deactivated. His email is d.torres@company.com. I'm the current Operations Manager and can verify my identity however needed." },

  { tag:"routine", dept:"Support",
    subject: "Duplicate charge on my account",
    body: "I was charged twice this month — once on the 1st and again on the 3rd for the same amount. I'm assuming it's an error. Can you look into it and issue a refund for the duplicate? I can provide the transaction IDs if needed." },

  { tag:"routine", dept:"Trips Team",
    subject: "Flight booking confirmation not received",
    body: "I booked a flight through the portal about 2 hours ago and got a confirmation number, but the email with the itinerary never showed up. It's not in my spam folder either. Can you resend it or pull up the booking details for me?" },

  // ── COMPLEX — multi-signal, Advisor tier ─────────────────────────────────────

  { tag:"complex", dept:"Operations",
    subject: "API integration failing after platform migration",
    body: `We completed our migration from v2 to v3 of your API last Thursday and our integration has been returning 403 errors intermittently — roughly 12% of all calls are failing. We've confirmed our API keys are valid and our request headers match the v3 spec. The failures seem to cluster between 2–4 PM EST. We've attached logs showing the pattern. We need a root cause analysis and need to understand whether this is a rate limiting issue, an infrastructure problem on your side, or a misconfiguration we've introduced. Our SLA with our downstream customers requires 99.9% uptime and we are currently breaching it.` },

  { tag:"complex", dept:"Sales",
    subject: "Enterprise contract renewal — pricing and SLA negotiation",
    body: `Our 3-year enterprise agreement expires in 47 days. We need to begin renewal discussions immediately. Our usage has grown significantly — we've added 4 new business units and our monthly token volume is up 340% since the original contract was signed. We'll need updated pricing that reflects this volume, a revised SLA with a 99.95% uptime guarantee, and dedicated support channel access. Our VP of Procurement and Legal will be involved in the final agreement. We'd like to schedule a call with your enterprise team this week.` },

  { tag:"complex", dept:"Operations",
    subject: "Data migration — 4-year historical records, 18 TB",
    body: `We're planning a full data migration from our legacy system to your platform. The dataset includes 4 years of customer transaction records, approximately 18 TB total. We need to understand your migration support process, the maximum ingestion rate, whether you have a dedicated migration team, and what the performance impact will be on our production environment during the transfer. We also need confirmation that all data will remain in the US-East region throughout the migration for compliance purposes.` },

  { tag:"complex", dept:"Support",
    subject: "Performance degradation investigation — 3 weeks of slow response times",
    body: `For the past three weeks our users have been experiencing significantly degraded performance — average response times have increased from 280ms to over 1,400ms. We've ruled out issues on our network, our CDN, and our internal infrastructure. The slowdown appears to be isolated to API calls to your platform. We need a formal investigation, a timeline of any infrastructure changes made on your side in the last 30 days, and a remediation plan. This is impacting our customer-facing product and we have already received complaints from 6 enterprise clients.` },

  { tag:"complex", dept:"Operations",
    subject: "Security incident — unauthorized access to shared account",
    body: `We detected unauthorized access to one of our shared service accounts at 03:14 AM EST this morning. Our security team identified login activity from an IP address in Eastern Europe that we've never seen before. The account had access to customer reporting dashboards. We need to know exactly what data was accessible, whether any records were exported, and what logs you have from that session. We are treating this as a potential data breach and our incident response protocol requires a written response within 4 hours.` },

  { tag:"complex", dept:"Sales",
    subject: "Multi-region deployment — architecture assessment required",
    body: `We are expanding into the EU and APAC markets and need to evaluate whether your platform can support a multi-region deployment. Specifically we need to understand data residency options (EU data must stay in the EU), latency profiles for users in Singapore and Frankfurt, whether you support active-active or active-passive failover, and your disaster recovery RTO and RPO commitments. Our architecture team will need a technical deep-dive call with your solutions engineering team before we can proceed with the expansion.` },

  { tag:"complex", dept:"Operations",
    subject: "Automated workflow breaking after recent update",
    body: `Three days ago one of your platform updates broke our overnight automated reconciliation workflow. The job runs at 2 AM daily and processes approximately 12,000 records. Since the update it has been failing at record 3,847 consistently with a memory allocation error. This affects our morning financial reporting which finance depends on by 7 AM. We need this escalated to engineering immediately. We cannot roll back our side of the integration without significant effort. We need either a hotfix or a configuration workaround today.` },

  { tag:"complex", dept:"Marketing",
    subject: "Campaign analytics discrepancy — revenue attribution off by 23%",
    body: `We're seeing a significant discrepancy between the revenue figures in your platform's campaign analytics and our internal CRM data. The platform is showing $847,000 in attributed revenue for our Q1 campaign but our CRM shows $654,000. That's a 23% gap. Before we present Q1 results to the board next Tuesday we need to understand the attribution methodology, whether there's a double-counting issue, and how the figures were calculated. Our CFO will not accept unexplained discrepancies of this magnitude.` },

  { tag:"complex", dept:"Operations",
    subject: "Disaster recovery test — requesting full failover simulation",
    body: `As part of our annual business continuity review we need to schedule a full disaster recovery test for our production integration. We need to simulate a complete primary region failure and measure actual failover time, data consistency, and recovery. This needs to be coordinated with your infrastructure team and must happen during a maintenance window. Our internal DR policy requires this to be completed by end of Q2. Please connect us with your enterprise reliability engineering team.` },

  { tag:"complex", dept:"Trips Team",
    subject: "Group travel booking error — 34 passengers, wrong departure city",
    body: `We have a critical booking error on reservation GRP-2026-8841. We booked travel for a 34-person executive offsite and the departure city was recorded as LAX instead of SFO. The travel date is in 11 days. We need this corrected immediately as we cannot rebook 34 business-class tickets at current prices. We need to know if the correction can be made without ticket reissuance, what the fare difference exposure is, and whether you have an emergency travel desk we can work with directly.` },

  // ── ESCALATE — sensitive terms, flags to Advisor ─────────────────────────────

  { tag:"escalate", dept:"Operations",
    subject: "GDPR right of access request — 72-hour regulatory deadline",
    body: `We have received a formal data subject access request under GDPR Article 15 from a customer in Germany. The request was submitted via email 70 hours ago. Under GDPR we are required to respond within 72 hours. We need to compile a complete record of all personal data held for this individual across all systems. Our Data Protection Officer is copied on this case. We need your platform's data export for this individual immediately to meet the regulatory deadline.` },

  { tag:"escalate", dept:"Sales",
    subject: "Contract dispute — breach of SLA terms, compensation claim",
    body: `We are formally notifying you of a breach of contract. Your platform experienced 4 hours and 22 minutes of unplanned downtime last month, exceeding the 99.9% uptime SLA in our agreement. Per Section 8.3 of our contract, we are entitled to service credits equal to 10x the monthly fee for each hour of excess downtime. We calculate this as $14,800 in service credits. Our legal team has reviewed this and is prepared to pursue the claim formally if not resolved within 15 business days.` },

  { tag:"escalate", dept:"Operations",
    subject: "HIPAA compliance review — data handling audit required",
    body: `Our compliance team is conducting an internal HIPAA audit ahead of our renewal of our BAA with your organization. We need documentation confirming that all PHI transmitted through your platform is encrypted at rest and in transit, that access logs are maintained for a minimum of 6 years, and that your workforce has completed HIPAA training within the last 12 months. We also need a current copy of your HIPAA risk assessment. Our audit deadline is June 15th.` },

  { tag:"escalate", dept:"Support",
    subject: "Potential regulatory violation — data shared with unauthorized third party",
    body: `We have reason to believe that customer data from our account may have been shared with a third-party vendor without our authorization. One of our customers received a marketing email from a company we have never shared their information with. The only common data source we can identify is your platform. We need a full audit of all data access and exports from our account in the last 90 days, a list of any third parties your platform shares data with, and written confirmation that our data has not been shared without consent.` },

  { tag:"escalate", dept:"Operations",
    subject: "Litigation hold — preserve all data for legal proceedings",
    body: `We are placing a formal litigation hold on all data associated with our account effective immediately. Our organization is involved in legal proceedings and our legal counsel has instructed us to preserve all records, logs, communications, and data exports associated with our account for the period January 1, 2024 through present. Please confirm receipt of this litigation hold notice and provide written confirmation that no data will be deleted, archived, or modified pending resolution of these proceedings.` },

  { tag:"escalate", dept:"Sales",
    subject: "Attorney review required — contract language update request",
    body: `Our legal department has flagged several clauses in our current Master Services Agreement that require negotiation before our renewal. Specifically, the indemnification language in Section 12, the limitation of liability cap in Section 15, and the data processing addendum need to be reviewed by both legal teams. Our attorney will be reaching out to your legal department directly. We need to pause the renewal clock until these contract terms are resolved.` },

  { tag:"escalate", dept:"Operations",
    subject: "Regulatory audit — external examiner requesting system access logs",
    body: `We are currently under examination by a federal regulatory body. The examiner has requested system access logs, API call records, and data processing logs for our integration for the period covering the last 18 months. We need to export this data in a format acceptable for regulatory submission. Our compliance officer needs to know what data is available, how long logs are retained, and whether we can receive a certified export suitable for submission to the examiner.` },

  { tag:"escalate", dept:"Support",
    subject: "Employee discrimination complaint — HR escalation",
    body: `I am submitting this case on behalf of a team member who has raised a formal discrimination complaint related to differential treatment in account access provisioning. The employee believes they were denied system access that was granted to peers in the same role. We need this reviewed at a senior level. Our HR department and legal counsel are aware of the situation. Please escalate this beyond the standard support queue.` },

  { tag:"escalate", dept:"Operations",
    subject: "Breach notification — potential unauthorized data exposure",
    body: `Our security team has identified a potential breach involving customer records stored in your platform. We discovered that an API key belonging to a former employee remained active for 47 days after their termination and was used to make 834 API calls during that period. We are initiating our breach notification protocol and need to understand exactly what data was accessible via that key, what operations were performed, and whether any data was exported. We may be required to notify affected customers under applicable breach notification laws.` },

  { tag:"escalate", dept:"Sales",
    subject: "Fraud investigation — suspicious account activity",
    body: `Our finance team has flagged suspicious activity on account #ACC-00447821. Over the past 10 days, the account has made 340 API calls from 6 different IP addresses across 4 countries, despite our organization operating only in the United States. We believe the account credentials may have been compromised and are being used fraudulently. We need an immediate account freeze, a full access log export, and your fraud investigation team to be involved. We are also filing a report with our cyber insurance provider.` },

  // ── BLOCKED — hard PII, stops cold ───────────────────────────────────────────

  { tag:"blocked", dept:"Support",
    subject: "Customer verification — financial details",
    body: `Customer called in to verify their identity. They provided their full social security number and date of birth for verification. SSN provided verbally and transcribed here for the case record. They are requesting a full account statement.` },

  { tag:"blocked", dept:"Support",
    subject: "Refund request — customer payment information",
    body: `Customer is requesting a refund to their original payment method. They provided their credit card number, the CVV on the back, and the card expiration date so we could locate the original transaction. Please process the $340 refund to the card on file.` },

  { tag:"blocked", dept:"Support",
    subject: "Account verification — bank details provided",
    body: `Customer provided their bank account number and routing number to set up ACH payment. They want to switch from credit card billing to direct debit. I have the full routing number and account number here in the case notes. Please update their billing method.` },

  { tag:"blocked", dept:"Operations",
    subject: "Employee onboarding — identity documents",
    body: `New hire starting Monday. HR has asked me to log their passport number and date of birth in the system for I-9 verification purposes. Their social security number is also needed for payroll setup. All three are included in this case for processing.` },

  { tag:"blocked", dept:"Support",
    subject: "Medical records request — patient information",
    body: `Patient is requesting a copy of their medical records. They provided their date of birth, social security number, and their diagnosis code for verification. Please process the release of records to the address on file.` },

  { tag:"blocked", dept:"Support",
    subject: "Wire transfer authorization — banking details",
    body: `Customer needs to authorize a wire transfer. They have provided their routing number, bank account number, and the receiving account details. The transfer amount is $24,500. Please confirm the wire can be processed today.` },

  { tag:"blocked", dept:"Operations",
    subject: "Vendor payment setup — financial account information",
    body: `Setting up a new vendor in the payment system. The vendor has provided their bank routing number, account number, and tax ID number for the W-9. I have all details here in the case for the finance team to process the first payment.` },

  { tag:"blocked", dept:"Support",
    subject: "Identity verification for account recovery",
    body: `Customer locked out of account and cannot use standard recovery. For escalated identity verification they have provided their full date of birth, the last four digits and full card number, and their social security number. Requesting manual account restoration.` },

  // ── VOICE — transcript with PII, runs Voice Guard first ──────────────────────

  { tag:"voice", dept:"Support",
    subject: "Call transcript — account verification",
    body: `Thanks for calling support, how can I help you today? Yeah hi I need to verify my account, I've been locked out. Sure I can help with that, can I get your name and some verification info? Of course, my name is Patricia Coleman, my date of birth is July nine nineteen seventy eight, and my social security number is, hold on let me find it, two one two, uh, forty four, eight eight nine one. Great I've got that. And can you confirm the last four of the card on file? Sure it's nine four five two. Perfect, I'm pulling up your account now.`, vg: true },

  { tag:"voice", dept:"Support",
    subject: "Call transcript — billing dispute",
    body: `Hi I'm calling about a charge I don't recognize. I see a charge for two hundred and forty dollars on my statement. Can you look into that? Of course, can you verify your account for me? Sure, my name is Marcus Webb, social security is three three three, fifty five, four four four four, and my date of birth is December third nineteen sixty five. Great. And what card did the charge appear on? It's my Visa, the number is four one one one, two two two two, three three three three, four four four four. And the CVV is nine two seven.`, vg: true },

  { tag:"voice", dept:"Support",
    subject: "Call transcript — insurance claim inquiry",
    body: `Hello I'm calling to check on my insurance claim. My member ID number is, let me look here, one one seven seven seven seven six seven six seven two two eight four. And my date of birth is April twenty second nineteen eighty one. I filed the claim about two weeks ago and haven't heard back. Can you check the status? Sure, and can I also get the last four of your social? The last four are eight eight zero nine. And your full social if you have it? Yeah it's five five five, sixty eight, nine nine zero one.`, vg: true },

  { tag:"voice", dept:"Trips Team",
    subject: "Call transcript — travel booking with payment",
    body: `Hi I need to book a last minute flight for tomorrow morning. I need to go from New York to Miami. Okay I can help with that, can I get a payment method? Yes my card number is four five three two zero one five seven zero one one nine eight four eight four. The expiration is oh nine twenty eight and the CVV is three one two. And the name on the card is Jennifer Thornton. And my date of birth for the traveler profile is August fourteenth nineteen eighty eight.`, vg: true },

  { tag:"voice", dept:"Operations",
    subject: "Call transcript — vendor payment authorization",
    body: `Yeah I'm calling to authorize the wire transfer for the Apex invoice. The amount is forty seven thousand five hundred dollars. I need to wire it to their account, the routing number is zero two one zero zero zero zero eight nine and the account number is four seven three three nine two eight eight one one. My authorization code is extension two two one, my employee ID is E four four nine seven, and my social security for the record is four five one, dash, two two, dash, four four four four.`, vg: true },

  { tag:"voice", dept:"Support",
    subject: "Call transcript — elderly customer struggling with verification",
    body: `Hello dear I'm having trouble with my account. Can you help me? Of course, can I get some information to verify your identity? Oh yes, my birthday is, now let me think, it's March, March the fifteenth, nineteen forty four. And my social, oh where did I write it down, it's nine, uh, one, two, three, uh wait no, my social security is five five five, twenty two, eleven eleven. I think that's right. And I have my card here, the number is, the whole number is four seven three nine eight eight four four zero one two three four five six seven.`, vg: true },

  // ── ADDITIONAL COMPLEX — variety industries ───────────────────────────────────

  { tag:"complex", dept:"Operations",
    subject: "Inventory system sync failure — 48 hours of data gap",
    body: `Our inventory management system stopped syncing with your platform 48 hours ago. We have approximately 2,200 product SKUs that have not had their stock levels updated since Tuesday at 6 PM. We've been managing manually but this is not sustainable. Our warehouse team is making fulfillment decisions based on stale data and we have already had 3 oversells on high-demand items. We need the sync restored immediately and a backfill of the 48-hour gap before our distribution center opens at 5 AM tomorrow.` },

  { tag:"complex", dept:"Marketing",
    subject: "Email deliverability crisis — 34% bounce rate on enterprise campaign",
    body: `We launched our largest campaign of the year yesterday — 240,000 contacts — and are seeing a 34% hard bounce rate. This is catastrophic for our sender reputation. Our email delivery rate should be above 98% based on our list hygiene. We need to understand immediately whether this is a sending domain issue, whether our IP has been blacklisted, or whether there is a platform configuration problem. Every hour this continues is damaging our ability to reach customers for the rest of the year.` },

  { tag:"complex", dept:"Sales",
    subject: "Competitor threatening litigation over customer poaching",
    body: `We've received a cease and desist letter from a competitor alleging that we improperly solicited their customers using data obtained through our integration with your platform. The letter claims we accessed a shared customer dataset that we were not authorized to use for prospecting. Our legal team is reviewing the allegation. We need to audit all data access from our account for the last 6 months to determine whether this claim has any merit and to prepare our legal response.` },

  { tag:"complex", dept:"Operations",
    subject: "Machine learning model performance degradation — accuracy dropped 18%",
    body: `Our production ML model that runs on top of your data pipeline has seen an 18% drop in prediction accuracy over the past 3 weeks. We've traced the degradation to changes in the data schema introduced in your March 14th platform update. Three feature columns now contain null values where they previously returned populated data. We need the original schema restored or documented so we can retrain our model. The model powers our demand forecasting and the accuracy drop is costing us an estimated $80,000/week in inefficient procurement decisions.` },

  { tag:"complex", dept:"Trips Team",
    subject: "Executive travel emergency — medical evacuation required",
    body: `We have an urgent situation. One of our senior executives is traveling in Bangkok and has been hospitalized with a cardiac event. We need to arrange medical evacuation back to the United States. We need your emergency travel desk to coordinate with our travel insurance provider, find an air ambulance capable of the journey, and arrange for a family member to travel to Bangkok immediately on the first available flight. This is life and safety critical and we need your most senior travel coordinator on this immediately.` },

  { tag:"complex", dept:"Operations",
    subject: "Regulatory reporting failure — quarterly submission at risk",
    body: `Our automated regulatory reporting workflow failed to generate this quarter's required submission package. The deadline to file with the SEC is in 36 hours. The workflow failure appears to be related to a timeout in your reporting API when the dataset exceeds 500,000 records. Our submission package has 1.2 million records. We need an emergency workaround, a higher timeout limit, or an alternative export method immediately. Missing this filing deadline carries substantial regulatory penalties.` },

  { tag:"complex", dept:"Sales",
    subject: "Partnership integration proposal — technical evaluation",
    body: `Our product team is proposing a deep integration between our platform and yours. We envision bidirectional data sync, shared customer identity management, and co-branded reporting. Before we can take this to our executive team we need a technical assessment of what's possible via your API, whether you have a partnership integration program, what the typical timeline and resource requirements are, and whether you have existing integrations we can reference. We'd like to involve both engineering and business development teams on both sides.` },

  { tag:"complex", dept:"Operations",
    subject: "Database performance bottleneck — queries timing out at scale",
    body: `Since we scaled our user base from 10,000 to 85,000 accounts last quarter our database query performance has degraded significantly. Queries that previously completed in under 100ms are now timing out after 30 seconds. We've already added read replicas and indexed the most frequently queried columns. The bottleneck appears to be in the reporting layer. We need your database engineering team to review our schema, query patterns, and recommend an architecture that will support 500,000 accounts within 18 months.` },

  { tag:"complex", dept:"Marketing",
    subject: "Attribution model dispute — $1.2M marketing budget decision at stake",
    body: `We need to resolve a fundamental disagreement between our marketing analytics platform and your attribution data before we finalize next year's $1.2M marketing budget allocation. Your platform is attributing 67% of Q1 conversions to paid search, while our analytics tool attributes 71% to organic and referral. The discrepancy is driving conflicting recommendations for budget allocation. We need your data science team to walk through your attribution methodology, window settings, and cross-device matching logic in detail.` },

  { tag:"complex", dept:"Support",
    subject: "Enterprise customer threatening public escalation",
    body: `Our largest enterprise customer — $2.4M ARR — has contacted our CEO directly to threaten a public post about service failures if their issues are not resolved by end of business today. They have experienced 7 separate incidents in the last 90 days and feel our support has been inadequate. Our CEO has committed to a resolution call at 3 PM today. We need a full incident history, root cause summaries for each event, and a proposed remediation and prevention plan ready before that call.` },

  // ── ADDITIONAL ROUTINE — variety ──────────────────────────────────────────────

  { tag:"routine", dept:"Support",
    subject: "Change notification email address",
    body: "I got married last month and changed my last name. I'd like to update my email address from jessica.smith@gmail.com to jessica.anderson@gmail.com. I'd also like to update the name on the account. Can you walk me through how to do this or make the change on your end?" },

  { tag:"routine", dept:"Trips Team",
    subject: "Add frequent flyer number to booking",
    body: "I forgot to add my Delta SkyMiles number when I booked my flight last week. My booking reference is DL-8847291 and my SkyMiles number is 4471882930. Can you add it before the flight so I get credit for the miles?" },

  { tag:"routine", dept:"Support",
    subject: "API rate limit question",
    body: "We're building an integration and hitting what looks like a rate limit but I can't find the exact numbers in your docs. Can you tell me what the rate limit is for the /records endpoint on a Business plan? And does the limit reset per minute or per hour?" },

  { tag:"routine", dept:"Marketing",
    subject: "Logo update on our account profile",
    body: "We rebranded last month and have a new company logo. I'd like to update the logo that shows in the platform and on any co-branded exports. I've attached the new PNG file in 400x400 and 1200x400 versions. Can you update it or tell me where I can do this myself?" },

  { tag:"routine", dept:"Operations",
    subject: "Scheduled maintenance window request",
    body: "We need to plan a maintenance window for a server upgrade on our end. The window would be Saturday May 31st from 2–6 AM EST. During that time our integration will be offline. I want to make sure there's nothing on your end that would conflict with that timing and that no automated jobs will fail because we're unreachable." },

  { tag:"routine", dept:"Support",
    subject: "Webhook endpoint not receiving events",
    body: "I set up a webhook endpoint last week but we're not receiving any events on our end even though the activity is happening. The endpoint URL is https://api.ourcompany.com/webhooks/fage and it returns 200. I've tested it manually and it works. Can you check whether events are being sent and whether there are any delivery failures logged?" },

  { tag:"routine", dept:"Sales",
    subject: "Non-profit discount inquiry",
    body: "We're a registered 501(c)(3) non-profit organization. Do you offer any discounts for non-profits? We're evaluating your platform for our volunteer coordination program and budget is a significant constraint for us. I can provide our EIN and tax exemption certificate if needed." },

  { tag:"routine", dept:"Support",
    subject: "Mobile app keeps crashing on login screen",
    body: "The iOS app crashes every time I try to log in. I've deleted and reinstalled it twice and the problem persists. I'm on an iPhone 15 Pro running iOS 18.3. The crash happens right after I enter my password and tap the login button. Can you check if there's a known issue or a beta fix available?" },

  // ── PRUNING HEAVY — maximum token savings demo ────────────────────────────────
  // These cases are loaded with email headers, reply chains, signatures,
  // legal disclaimers, and HTML. The pruner strips all of it.

  { tag:"complex", dept:"Support",
    subject: "RE: RE: RE: RE: RE: RE: RE: Login broken — Ticket #77431",
    body: `From: david.morrison@enterprisecorp.com
To: support@company.com
CC: it-team@enterprisecorp.com; manager@enterprisecorp.com; helpdesk@enterprisecorp.com
Date: Wednesday, May 27, 2026, 9:14 AM
Subject: RE: RE: RE: RE: RE: RE: RE: Login broken — Ticket #77431
X-Mailer: Microsoft Outlook 16.0
X-Originating-IP: 192.168.1.44
X-Spam-Status: No
MIME-Version: 1.0

Still broken. Nothing has changed on our end. This has been going on for 7 days now.

David Morrison | Senior IT Administrator
Enterprise Corporation | IT Infrastructure Division
📍 1200 Business Park Drive, Suite 800, Dallas, TX 75201
📞 Office: (214) 555-4400 | 📱 Mobile: (214) 555-8812 | 📠 Fax: (214) 555-4401
✉ david.morrison@enterprisecorp.com | Slack: @dmorrison
🌐 www.enterprisecorp.com | LinkedIn: linkedin.com/in/davidmorrison
Microsoft Certified Systems Engineer | CompTIA Security+
Enterprise Corporation — Powering Business Since 1998

CONFIDENTIALITY NOTICE: This electronic message and any files transmitted with it are intended exclusively for the individual or entity to whom they are addressed. This communication may contain information that is proprietary, privileged, confidential or otherwise legally exempt from disclosure. If you are not the named addressee, you are not authorized to read, print, retain, copy or disseminate this message or any part of it. If you have received this message in error, please notify the originator immediately and destroy all copies of the original message and any attachments. This message has been scanned for malware by Enterprise Corp IT Security (Powered by CrowdStrike Falcon). Enterprise Corporation is an Equal Opportunity Employer.

-----Original Message-----
From: Support Team [mailto:support@company.com]
Sent: Tuesday, May 26, 2026 4:32 PM
To: David Morrison
CC: it-team@enterprisecorp.com
Subject: RE: RE: RE: RE: RE: RE: Login broken — Ticket #77431

Hi David,

Thank you for your continued patience as we work through this matter. Our engineering team has been notified and we have escalated this to Tier 2 support. We understand this is frustrating and we appreciate your understanding. A senior engineer will review your case within the next 2 business days. We value your partnership and remain committed to resolving this as quickly as possible.

Best regards,
Amanda Torres | Customer Support Specialist
support@company.com | 1-800-555-2000 ext. 4421
Hours: Monday–Friday 8AM–6PM PT
Ranked #1 in Customer Satisfaction — G2 Spring 2026

This email and any attachments are confidential. Company Inc. is registered in Delaware, USA. Privacy policy: www.company.com/privacy. This email was automatically scanned by Proofpoint Email Security.

-----Original Message-----
From: David Morrison [mailto:david.morrison@enterprisecorp.com]
Sent: Tuesday, May 26, 2026 10:15 AM
To: Support Team
Subject: RE: RE: RE: RE: RE: Login broken — Ticket #77431

This is day 6. Our entire finance department cannot access their accounts. 22 users are locked out. I've already tried clearing cache, different browsers, incognito mode, and a different network. None of it works. I need an engineer on this today.

David Morrison | Senior IT Administrator | Enterprise Corporation
📞 (214) 555-4400 | ✉ david.morrison@enterprisecorp.com

CONFIDENTIALITY NOTICE: This message is intended exclusively for the named recipient. If received in error notify sender and destroy all copies. Scanned by CrowdStrike Falcon.

-----Original Message-----
From: Support Team [mailto:support@company.com]
Sent: Monday, May 25, 2026 3:30 PM
To: David Morrison
Subject: RE: RE: RE: RE: Login broken — Ticket #77431

Hi David, thank you for following up. We've escalated to our Tier 2 team and expect to have an update within 1-2 business days. We sincerely apologize for the inconvenience and appreciate your continued patience.

James Whitfield | Customer Support Representative — Tier 1
📞 1-800-555-9000 | ✉ j.whitfield@company.com
Ranked #1 in Customer Satisfaction — G2 Spring 2026
This email and any attachments are confidential. Scanned by Proofpoint.

-----Original Message-----
From: David Morrison
Sent: Monday, May 25, 2026 9:02 AM
To: Support Team
Subject: RE: RE: RE: Login broken — Ticket #77431

Still no resolution. Five days in. I've escalated internally and my VP is now asking for daily updates. When will this be fixed?

David Morrison | Enterprise Corporation | (214) 555-4400
CONFIDENTIALITY NOTICE: Unauthorized disclosure prohibited. Scanned by CrowdStrike.

-----Original Message-----
From: Support Team
Sent: Friday, May 23, 2026 2:17 PM
Subject: RE: RE: Login broken — Ticket #77431

Hi David, we've received your follow-up and have assigned this to our Tier 2 team. Please try clearing your browser cache and cookies and attempt login using an incognito window. You can also check our status page at status.company.com.

Sarah Mitchell | Support Specialist | support@company.com | 1-800-555-2000
This email is confidential. Scanned by Proofpoint.

-----Original Message-----
From: David Morrison
Sent: Thursday, May 22, 2026 8:45 AM
Subject: Login broken — Ticket #77431

Our entire finance team cannot log in. The error says "Authentication service unavailable." This started yesterday morning after your platform update. 22 users affected. We need this fixed today.` },

  { tag:"complex", dept:"Operations",
    subject: "FW: FW: FW: FW: FW: Server migration — data sync failure",
    body: `<!DOCTYPE html><html><head><style>body{font-family:Calibri,Arial,sans-serif;font-size:14px;color:#1a1a1a}.disclaimer{font-size:10px;color:#888;border-top:1px solid #ccc;margin-top:20px;padding-top:10px}.sig{color:#555;font-size:12px}table{border-collapse:collapse}td{padding:2px 6px}</style></head><body>
<p>Team,</p>
<p>Forwarding again. Still waiting on resolution. The data sync has been failing for 4 days and our quarterly close is tomorrow morning.</p>
<p class="sig">
Rachel Kim, CPA, MBA<br/>
Vice President of Finance &amp; Accounting<br/>
Meridian Global Holdings — Finance Division<br/>
📍 One Financial Plaza, 28th Floor, Chicago, IL 60601<br/>
📞 Direct: (312) 555-7700 | 📱 Cell: (312) 555-9914 | 📠 Fax: (312) 555-7701<br/>
✉ r.kim@meridianglobal.com<br/>
🌐 www.meridianglobal.com | Bloomberg: MRDNG:US<br/>
CPA License #IL-449821 | CFA Level III Candidate<br/>
<img src="cid:meridian-logo" alt="Meridian Global Holdings" width="200"/><br/>
<em>Meridian Global Holdings — Excellence in Financial Management Since 1974</em><br/>
<em>Fortune 500 | NYSE: MRDNG | ISO 27001 Certified | SOC 2 Type II Audited</em>
</p>
<div class="disclaimer">CONFIDENTIALITY NOTICE: This electronic message and any attachments are for the exclusive and confidential use of the intended recipient. This communication may contain information that is proprietary, privileged, or otherwise legally exempt from disclosure. If you are not the intended recipient please notify the sender immediately and destroy all copies. Meridian Global Holdings complies with all applicable data protection laws including GDPR and CCPA. This message has been scanned for malware by Meridian IT Security (CrowdStrike Falcon + Microsoft Defender). Meridian Global Holdings is a publicly traded company. Any information contained in this communication that relates to the business affairs of Meridian Global Holdings may constitute material non-public information. Trading on such information may violate securities laws. This email was sent using Microsoft Exchange Server 2019 on-premises. For IT support please contact the Meridian Help Desk at helpdesk@meridianglobal.com or call (312) 555-HELP (4357).</div>
<hr style="border:none;border-top:1px solid #ccc;margin:20px 0"/>
<p style="font-size:12px;color:#555"><strong>-----Original Message-----</strong><br/>From: Enterprise Support &lt;support@company.com&gt;<br/>Sent: Tuesday, May 26, 2026 5:18 PM<br/>Subject: RE: FW: FW: FW: FW: Server migration — data sync failure</p>
<p>Hi Rachel, thank you for your patience. Our engineering team has been notified and we are treating this as a high priority. We expect to have an update within 4 business hours. We sincerely apologize for the disruption this has caused to your operations.</p>
<p class="sig">Kevin Rasmussen<br/>Senior Customer Success Manager<br/>📞 (628) 555-4490 | ✉ k.rasmussen@company.com<br/>Certified Customer Success Professional<br/><em>"Customers First, Always."</em></p>
<div class="disclaimer">This email is confidential. Company Inc. | www.company.com. Scanned by Proofpoint.</div>
<hr style="border:none;border-top:1px solid #ccc;margin:20px 0"/>
<p style="font-size:12px;color:#555"><strong>-----Original Message-----</strong><br/>From: Rachel Kim &lt;r.kim@meridianglobal.com&gt;<br/>Sent: Tuesday, May 26, 2026 9:44 AM</p>
<p>This is day 4. Our general ledger sync has been broken since Saturday. We cannot close our books for Q1 without this data. I am formally escalating to your VP of Customer Success. If this is not resolved by 8 AM tomorrow I will be contacting your CEO directly.</p>
<p class="sig">Rachel Kim, CPA, MBA | VP Finance | Meridian Global Holdings<br/>📞 (312) 555-7700 | ✉ r.kim@meridianglobal.com</p>
<div class="disclaimer">CONFIDENTIALITY NOTICE: Unauthorized disclosure prohibited. GDPR and CCPA compliant. Scanned by CrowdStrike Falcon.</div>
<hr style="border:none;border-top:1px solid #ccc;margin:20px 0"/>
<p style="font-size:12px;color:#555"><strong>-----Original Message-----</strong><br/>Sent: Monday, May 25, 2026 2:30 PM</p>
<p>Still broken. Four attempts to reconnect the sync have all failed. Error: "Database connection timeout after 30000ms." Our IT team has confirmed the issue is on your platform side, not ours.</p>
<hr style="border:none;border-top:1px solid #ccc;margin:20px 0"/>
<p style="font-size:12px;color:#555"><strong>-----Original Message-----</strong><br/>Sent: Saturday, May 23, 2026 11:02 AM</p>
<p>The data sync between our ERP system and your platform stopped working at approximately 6 PM Friday. We are getting timeout errors on every sync attempt. This is blocking our quarter-close process which must complete by Wednesday morning.</p>
</body></html>` },

  { tag:"escalate", dept:"Sales",
    subject: "RE: RE: RE: RE: RE: RE: RE: RE: RE: Contract renewal — legal review",
    body: `From: margaret.osei@globalventures.com
To: enterprise@company.com
CC: legal@globalventures.com; procurement@globalventures.com; cfo@globalventures.com; board-secretary@globalventures.com
Date: Wednesday, May 27, 2026, 8:22 AM
Subject: RE: RE: RE: RE: RE: RE: RE: RE: RE: Contract renewal — legal review
X-Mailer: Microsoft Outlook 16.0
Importance: High

Forwarding the entire chain for context. Our attorney has reviewed the MSA and we have 14 specific items that require negotiation before we can sign. The contract expires in 12 days. We need a call with your General Counsel this week.

Margaret Osei, JD, MBA
Chief Procurement Officer & Deputy General Counsel
Global Ventures International — Legal & Procurement Division
📍 500 Park Avenue, 42nd Floor, New York, NY 10022
📞 Direct: (212) 555-3300 | 📱 Mobile: (212) 555-9981 | 📠 Fax: (212) 555-3301
✉ m.osei@globalventures.com | Assistant: assistant@globalventures.com
🌐 www.globalventures.com | LinkedIn: linkedin.com/in/margaretosei
Admitted to the Bar: New York, California, DC
Global Ventures International — Assets Under Management: $4.2B

CONFIDENTIALITY NOTICE: This communication is from an attorney and may contain attorney-client privileged information and/or work product. It is intended only for the use of the individual or entity to which it is addressed. If you are not the intended recipient, you are hereby notified that any disclosure, copying, distribution, or use of the contents of this transmission is strictly prohibited. If you have received this communication in error, please immediately notify the sender and destroy this communication and all copies thereof. This message has been scanned for malware. Global Ventures International is regulated by the SEC and FINRA.

-----Original Message-----
From: Enterprise Sales [mailto:enterprise@company.com]
Sent: Monday, May 25, 2026 3:15 PM
Subject: RE: RE: RE: RE: RE: RE: RE: RE: Contract renewal — legal review

Hi Margaret, thank you for your patience throughout this process. We've escalated to our Legal team and they will be in touch within 2 business days. We remain committed to finding mutually agreeable terms and look forward to continuing our partnership.

Thomas Bradley | VP Enterprise Sales
📞 (415) 555-8800 | ✉ t.bradley@company.com
Certified: Salesforce, HubSpot, Challenger Sales
This email is confidential. Scanned by Proofpoint.

-----Original Message-----
From: Margaret Osei
Sent: Monday, May 25, 2026 10:08 AM
Subject: RE: RE: RE: RE: RE: RE: RE: Contract renewal

Our legal team has completed its review. The indemnification clause in Section 12, the limitation of liability cap in Section 15, the data processing addendum, the governing law clause in Section 22, the auto-renewal terms, and the IP ownership language in Section 8 all require revision before we can execute. We will not sign the agreement as currently written. Please have your General Counsel contact ours directly.

Margaret Osei, JD, MBA | CPO & Deputy GC | Global Ventures International
📞 (212) 555-3300 | ✉ m.osei@globalventures.com
CONFIDENTIALITY NOTICE: Attorney-client privileged. Unauthorized disclosure prohibited.

-----Original Message-----
Sent: Friday, May 23, 2026
Subject: RE: RE: RE: RE: RE: Contract renewal

We received the redlined MSA. Our legal team is reviewing and will respond by end of next week. The auto-renewal clause is particularly concerning given the 90-day notice requirement.

CONFIDENTIALITY NOTICE: Attorney-client privileged communication. Unauthorized disclosure prohibited. Scanned for malware.

-----Original Message-----
Sent: Wednesday, May 21, 2026
Subject: RE: RE: RE: Contract renewal

Sending our redlines to the MSA for your review. We have significant concerns about the indemnification, liability cap, and data processing terms. Our attorney will follow up separately.

CONFIDENTIALITY NOTICE: This communication is attorney-client privileged.` },

  { tag:"complex", dept:"Operations",
    subject: "RE: RE: RE: RE: RE: RE: RE: RE: Compliance audit — system access logs",
    body: `From: compliance-team@healthnetwork.org
To: enterprise-support@company.com
CC: ciso@healthnetwork.org; legal@healthnetwork.org; audit@healthnetwork.org; dpo@healthnetwork.org; ceo-office@healthnetwork.org
Date: Wednesday, May 27, 2026, 7:55 AM
Subject: RE: RE: RE: RE: RE: RE: RE: RE: Compliance audit — system access logs
X-Mailer: Microsoft Outlook 16.0
Sensitivity: Confidential

Still waiting. Our external auditor arrives on Monday. We must have the complete system access log export before then.

Dr. Patricia Okonkwo-Williams, MD, MBA, CHCIO, CPHIMS
Chief Health Informatics & Compliance Officer
National Health Network — Enterprise Technology & Compliance Division
📍 4400 Health Sciences Drive, Suite 2200, Atlanta, GA 30339
📞 Office: (404) 555-3310 | 📱 Cell: (404) 555-8821 | 📠 Fax: (404) 555-3311
✉ p.okonkwo-williams@healthnetwork.org
🌐 www.healthnetwork.org
Diplomate, American Board of Medical Informatics
Fellow, American College of Healthcare Executives
Certified in Healthcare Information and Management Systems (CPHIMS)
HIPAA Privacy Officer | HITECH Compliance Officer
National Health Network — Serving 2.4M patients across 14 states
Accredited by The Joint Commission | HIMSS Stage 7 | HITRUST CSF Certified | ISO 27001

CONFIDENTIALITY NOTICE: This electronic message and any files transmitted with it are intended exclusively for the individual or entity to whom it is addressed. This communication may contain protected health information (PHI) subject to HIPAA Privacy and Security Rules (45 CFR Parts 160 and 164), information protected by attorney-client privilege, and/or information that is otherwise legally exempt from disclosure. If you are not the named addressee, you are strictly prohibited from reading, copying, distributing, or taking any action based on the contents of this communication. If received in error, immediately notify the sender and permanently destroy all copies. Unauthorized disclosure of PHI may result in civil penalties up to $1.9 million per violation category per year and criminal penalties under 42 U.S.C. § 1320d-6. This message has been scanned for malware by National Health Network IT Security (CrowdStrike Falcon + Carbon Black). National Health Network is a not-for-profit health system. This email was transmitted using TLS 1.3 encryption.

-----Original Message-----
From: Enterprise Support [mailto:enterprise-support@company.com]
Sent: Tuesday, May 26, 2026 6:18 PM
Subject: RE: RE: RE: RE: RE: RE: RE: Compliance audit — system access logs

Dr. Okonkwo-Williams, thank you for your patience. We have escalated this to our compliance team and our data engineering team is working on preparing the export. We understand the urgency and will provide an update by end of business tomorrow.

Kevin Rasmussen | Senior CSM | k.rasmussen@company.com | (628) 555-4490
CCSP Certified | This email is confidential. Scanned by Proofpoint.

-----Original Message-----
From: Dr. Patricia Okonkwo-Williams
Sent: Tuesday, May 26, 2026 9:44 AM
Subject: RE: RE: RE: RE: RE: RE: Compliance audit

This is the fourth time I am requesting the 18-month system access log export. Our HIPAA compliance audit begins Monday. The external auditor requires: all API access logs, user authentication records, data export events, and permission change logs for the period January 1, 2025 through present. The export must be in a format suitable for regulatory submission. We also need your Business Associate Agreement updated to reflect the 2024 HIPAA Omnibus amendments.

CONFIDENTIALITY NOTICE: Contains PHI. HIPAA protected. Unauthorized disclosure prohibited.

-----Original Message-----
Sent: Monday, May 25, 2026
Subject: RE: RE: RE: RE: RE: Compliance audit

Still waiting for the log export and updated BAA. Our audit starts in 5 days.

CONFIDENTIALITY NOTICE: PHI protected under HIPAA 45 CFR Parts 160 and 164. Scanned by CrowdStrike.

-----Original Message-----
Sent: Friday, May 23, 2026
Subject: RE: RE: RE: RE: Compliance audit

We need the access logs and BAA update urgently. External auditor confirmed for Monday.

CONFIDENTIALITY NOTICE: Contains PHI. HIPAA protected.` },

  { tag:"routine", dept:"Sales",
    subject: "RE: RE: RE: RE: RE: RE: Pricing inquiry — annual plan",
    body: `From: james.thornton@acmecorp.com
To: sales@company.com
CC: it@acmecorp.com; finance@acmecorp.com
Date: Wednesday, May 27, 2026, 10:02 AM
Subject: RE: RE: RE: RE: RE: RE: Pricing inquiry — annual plan

Hi, just following up. Still haven't received the quote.

James Thornton | Director of Technology
Acme Corporation | Technology Division
📍 800 Innovation Blvd, Suite 300, Austin, TX 78701
📞 Direct: (512) 555-2200 | 📱 Mobile: (512) 555-8844
✉ j.thornton@acmecorp.com
🌐 www.acmecorp.com
PMP Certified | AWS Solutions Architect Associate

This email and any attachments are confidential and may be privileged. If you have received this email in error, please notify the sender immediately and delete it. Acme Corporation | www.acmecorp.com. This email was scanned by Symantec Email Security.

-----Original Message-----
From: Sales Team [mailto:sales@company.com]
Sent: Monday, May 26, 2026 2:44 PM
To: James Thornton
Subject: RE: RE: RE: RE: RE: Pricing inquiry — annual plan

Hi James, thank you for your interest. Our team is preparing a custom quote based on your requirements. We will have it to you by end of this week. We appreciate your patience.

Sarah Mitchell | Account Executive | s.mitchell@company.com | (415) 555-3300
Salesforce Certified | HubSpot Certified | This email is confidential. Scanned by Proofpoint.

-----Original Message-----
From: James Thornton
Sent: Friday, May 23, 2026 9:15 AM
Subject: RE: RE: RE: RE: Pricing inquiry — annual plan

Hi, it's been a week. Still waiting on the quote for the annual Business plan for 25 users. Our Q2 budget needs to be finalized by June 1st.

James Thornton | Director of Technology | Acme Corporation | (512) 555-2200
This email is confidential. Scanned by Symantec.

-----Original Message-----
From: Sales Team
Sent: Monday, May 19, 2026
Subject: RE: RE: RE: Pricing inquiry

Hi James, thank you for reaching out. We'd love to help you find the right plan. Could you confirm the number of users, your primary use case, and whether you need SSO or advanced security features? We'll get you a custom quote.

This email is confidential. Company Inc. | www.company.com. Scanned by Proofpoint.

-----Original Message-----
From: James Thornton
Sent: Monday, May 19, 2026
Subject: Pricing inquiry — annual plan

Hi, we're interested in the annual Business plan for approximately 25 users. Can you send me pricing?

James Thornton | Acme Corporation` },

  { tag:"complex", dept:"Marketing",
    subject: "RE: RE: RE: RE: RE: RE: RE: Campaign attribution discrepancy — board presentation tomorrow",
    body: `From: cmo@nexusbrands.com
To: enterprise-support@company.com
CC: cfo@nexusbrands.com; analytics@nexusbrands.com; board-prep@nexusbrands.com; legal@nexusbrands.com
Date: Wednesday, May 27, 2026, 7:08 AM
Subject: RE: RE: RE: RE: RE: RE: RE: Campaign attribution discrepancy — board presentation tomorrow
X-Priority: 1
X-Mailer: Microsoft Outlook 16.0

URGENT. Board presentation is at 9 AM tomorrow. The $1.2M budget discrepancy between your platform and our internal data is still unresolved. We cannot present to the board with unexplained numbers.

Alexandra Chen | Chief Marketing Officer
Nexus Brands Group — Global Marketing Division
📍 200 Brand Avenue, 15th Floor, San Francisco, CA 94105
📞 Direct: (415) 555-6600 | 📱 Cell: (415) 555-9920 | 📠 Fax: (415) 555-6601
✉ a.chen@nexusbrands.com | Twitter: @alexchen_cmo
🌐 www.nexusbrands.com | LinkedIn: linkedin.com/in/alexandrachen
Forbes CMO Next 2025 | AdAge Top 40 Under 40 | Cannes Lions Grand Prix Winner
Nexus Brands Group — $2.1B Revenue | 34 Countries | 180 Brands

This email and any attachments are strictly confidential and intended solely for the named recipient. If you received this in error, notify the sender immediately and delete all copies. Nexus Brands Group complies with GDPR, CCPA, and all applicable privacy regulations. This communication may contain material non-public business information. This email was automatically scanned by Nexus Brands IT Security.

-----Original Message-----
From: Enterprise Support [mailto:enterprise-support@company.com]
Sent: Tuesday, May 26, 2026 8:44 PM
Subject: RE: RE: RE: RE: RE: RE: Campaign attribution discrepancy

Hi Alexandra, our data science team has been notified and is investigating the attribution discrepancy. We are treating this as urgent given your board presentation timeline. We will have an explanation ready by 7 AM tomorrow morning.

Kevin Rasmussen | Senior CSM | (628) 555-4490 | k.rasmussen@company.com
This email is confidential. Scanned by Proofpoint.

-----Original Message-----
From: Alexandra Chen
Sent: Tuesday, May 26, 2026 3:22 PM
Subject: RE: RE: RE: RE: RE: Campaign attribution discrepancy

This is unacceptable. Your platform shows $847,000 attributed to our Q1 spring campaign. Our Salesforce CRM shows $654,000. Our Google Analytics shows $612,000. Three different numbers for the same campaign. Our CFO and board will not accept this tomorrow. I need your data science team on a call today, not tomorrow.

Alexandra Chen | CMO | Nexus Brands Group | (415) 555-6600
CONFIDENTIALITY NOTICE: Proprietary business information. Unauthorized disclosure prohibited.

-----Original Message-----
Sent: Monday, May 25, 2026
Subject: RE: RE: RE: Campaign attribution discrepancy

Week 2 with no explanation for the $1.2M discrepancy. Board presentation rescheduled to Wednesday. This must be resolved before then.

-----Original Message-----
Sent: Wednesday, May 21, 2026
Subject: Attribution discrepancy — urgent

Our platform shows $847K attributed revenue for Q1 spring campaign. Our CRM shows $654K. That is a $193,000 discrepancy on a campaign with $1.2M in associated spend. We need an explanation before our board presentation next week.` },

  { tag:"escalate", dept:"Operations",
    subject: "RE: RE: RE: RE: RE: RE: RE: RE: RE: RE: Litigation hold — data preservation required",
    body: `From: general-counsel@fortresscapital.com
To: legal@company.com
CC: ceo@fortresscapital.com; cfo@fortresscapital.com; compliance@fortresscapital.com; outside-counsel@millerlaw.com; board-chair@fortresscapital.com
Date: Wednesday, May 27, 2026, 6:45 AM
Subject: RE: RE: RE: RE: RE: RE: RE: RE: RE: RE: Litigation hold — data preservation required
X-Priority: 1
Sensitivity: Confidential
X-Mailer: Microsoft Outlook 16.0

NOTICE OF LITIGATION HOLD AND PRESERVATION DEMAND — THIRD REQUEST

This is our third written request. We have not received written confirmation of data preservation. Our outside counsel is prepared to seek emergency injunctive relief if written confirmation is not received by 5 PM today.

Jonathan Reeves, JD, LLM
General Counsel & Chief Legal Officer
Fortress Capital Management — Legal & Compliance Division
📍 245 Park Avenue, 38th Floor, New York, NY 10167
📞 Direct: (212) 555-5500 | 📱 Mobile: (212) 555-9971 | 📠 Fax: (212) 555-5501
✉ j.reeves@fortresscapital.com | Assistant: legal-assistant@fortresscapital.com
🌐 www.fortresscapital.com
New York State Bar | District of Columbia Bar | U.S. Supreme Court Bar
Fortress Capital Management — AUM: $18.7B | SEC-Registered Investment Adviser

PRIVILEGED AND CONFIDENTIAL — ATTORNEY-CLIENT COMMUNICATION: This message constitutes a privileged and confidential attorney-client communication and/or attorney work product. It is intended solely for the individual or entity to whom it is addressed. Any interception, review, retransmission, dissemination, or other use of, or taking of any action upon, this information by persons or entities other than the intended recipient is prohibited and may subject you to criminal and civil liability. If you received this in error, contact the sender immediately and destroy all copies. This communication is also subject to the attorney-client privilege and work-product doctrine. Fortress Capital Management is a registered investment adviser with the U.S. Securities and Exchange Commission. Communications from Fortress Capital Management may constitute material non-public information. Trading based on such information may violate federal securities laws including Section 10(b) of the Securities Exchange Act of 1934. This email was sent via encrypted transport (TLS 1.3) and has been scanned by Fortress Capital IT Security (Palo Alto Cortex XDR).

-----Original Message-----
From: Legal Department [mailto:legal@company.com]
Sent: Tuesday, May 26, 2026 11:30 AM
Subject: RE: RE: RE: RE: RE: RE: RE: RE: RE: Litigation hold

Dear Mr. Reeves, we have received your litigation hold notice and have escalated to our General Counsel. We will provide written confirmation of data preservation measures by end of business today. We take litigation hold obligations seriously and are committed to full compliance.

Maria Santos | Associate General Counsel | legal@company.com
PRIVILEGED AND CONFIDENTIAL — ATTORNEY-CLIENT COMMUNICATION. This email is confidential. Scanned by Proofpoint.

-----Original Message-----
From: Jonathan Reeves
Sent: Tuesday, May 26, 2026 9:02 AM
Subject: RE: RE: RE: RE: RE: RE: RE: RE: Litigation hold — SECOND REQUEST

This is our second request for written confirmation. The litigation hold covers all data, communications, logs, and records associated with account #ENT-00884421 for the period January 1, 2023 through present. Any destruction, alteration, or failure to preserve this data may constitute spoliation of evidence and will be raised with the court.

Jonathan Reeves, JD, LLM | GC & CLO | Fortress Capital Management
PRIVILEGED AND CONFIDENTIAL — ATTORNEY-CLIENT COMMUNICATION.

-----Original Message-----
Sent: Monday, May 25, 2026
Subject: Litigation hold — data preservation demand

We are placing a formal litigation hold on all data associated with our account effective immediately. Preserve all records. Written confirmation required within 24 hours. Outside counsel: Miller & Associates, (212) 555-8800.

PRIVILEGED AND CONFIDENTIAL — ATTORNEY-CLIENT COMMUNICATION.` },

  { tag:"complex", dept:"Support",
    subject: "RE: RE: RE: RE: RE: RE: RE: RE: Platform outage — SLA breach — $2.4M client at risk",
    body: `From: vp-operations@titanlogistics.com
To: enterprise-support@company.com; escalations@company.com; ceo@company.com
CC: cto@titanlogistics.com; cfo@titanlogistics.com; legal@titanlogistics.com; enterprise-client-lead@titanlogistics.com
Date: Wednesday, May 27, 2026, 6:30 AM
Subject: RE: RE: RE: RE: RE: RE: RE: RE: Platform outage — SLA breach — $2.4M client at risk
X-Priority: 1
Importance: High

Day 3. Still down. We are formally documenting this as an SLA breach. Our $2.4M annual contract is under review.

Robert Nakamura | Vice President of Operations & Technology
Titan Logistics Group — Enterprise Operations Division
📍 1500 Harbor Drive, Suite 1200, Long Beach, CA 90802
📞 Direct: (562) 555-7700 | 📱 Cell: (562) 555-9944 | 📠 Fax: (562) 555-7701
✉ r.nakamura@titanlogistics.com
🌐 www.titanlogistics.com | SCAC: TITL
Certified in Production and Inventory Management (CPIM)
Lean Six Sigma Black Belt | Project Management Professional (PMP)
Titan Logistics Group — 14,000 Shipments Daily | 47 Countries | ISO 9001 Certified

CONFIDENTIALITY NOTICE: This electronic communication is intended only for the named recipient. If received in error, notify the sender and delete all copies. Titan Logistics Group is an interstate carrier subject to federal regulation. This email was scanned by Titan IT Security (Palo Alto + Splunk SIEM).

-----Original Message-----
From: Enterprise Support [mailto:escalations@company.com]
Sent: Tuesday, May 26, 2026 9:55 PM
Subject: RE: RE: RE: RE: RE: RE: RE: Platform outage — SLA breach

Robert, our engineering team has been working around the clock on this. We have identified the root cause — a corrupted database index following last Friday's maintenance update — and are in the process of rebuilding it. We estimate 4-6 hours to full restoration. We sincerely apologize for the impact this has had on your operations.

Director of Enterprise Support | escalations@company.com | 1-800-555-9000
This email is confidential. Scanned by Proofpoint.

-----Original Message-----
From: Robert Nakamura
Sent: Tuesday, May 26, 2026 4:18 PM
Subject: RE: RE: RE: RE: RE: RE: Platform outage — SLA breach

Our shipment tracking system has been completely down for 72 hours. We have 847 shipments in transit with no visibility. Three of our largest retail clients — combined annual revenue of $2.4M — have escalated to their own executive teams. Our legal team is reviewing the SLA breach provisions in our contract. We need the system restored immediately and a full post-incident report within 48 hours of restoration.

Robert Nakamura | VP Operations | Titan Logistics Group | (562) 555-7700
CONFIDENTIALITY NOTICE: Proprietary business information.

-----Original Message-----
Sent: Monday, May 25, 2026
Subject: RE: RE: RE: Platform outage — Day 2

Still down. 48 hours. Our on-call operations team has been manually processing shipments. This is not sustainable. We need an ETA.

CONFIDENTIALITY NOTICE: Proprietary. Scanned by Palo Alto.

-----Original Message-----
Sent: Sunday, May 24, 2026
Subject: RE: RE: Platform outage

24 hours with no resolution and no ETA. Our CEO is now involved. We need executive escalation on your side.

-----Original Message-----
Sent: Saturday, May 23, 2026
Subject: Platform outage — shipment tracking down

Our shipment tracking integration went down at 11 PM Friday. We cannot track any of our 847 active shipments. This is mission critical. We need immediate resolution.` },

];

// Shuffle tracker — ensures no repeats until all 60 are shown
let _libraryQueue = [];

function _getNextCase() {
  if (_libraryQueue.length === 0) {
    // Refill and shuffle
    _libraryQueue = CASE_LIBRARY.map((_, i) => i);
    for (let i = _libraryQueue.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [_libraryQueue[i], _libraryQueue[j]] = [_libraryQueue[j], _libraryQueue[i]];
    }
  }
  return CASE_LIBRARY[_libraryQueue.pop()];
}

function generateCase() {
  const c = _getNextCase();

  // Clear scenario button highlights
  document.querySelectorAll(".scenario-btn").forEach(el => el.classList.remove("active"));
  activeScenario = null;

  document.getElementById("caseSubject").value = c.subject;
  document.getElementById("caseBody").value    = c.body;

  // Set dept if present
  if (c.dept) {
    const sel = document.getElementById("deptSelect");
    if ([...sel.options].some(o => o.value === c.dept)) sel.value = c.dept;
  }

  // Voice Guard
  const vgRow = document.getElementById("vgToggleRow");
  if (c.vg) {
    vgRow.style.display = "flex";
    setVoiceGuard(true);
  } else {
    vgRow.style.display = "none";
    setVoiceGuard(false);
  }

  // Flash the generate button to confirm
  const btn = document.getElementById("generateBtn");
  if (btn) {
    btn.textContent = "✓ Loaded";
    setTimeout(() => { btn.textContent = "⚡ Generate Case"; }, 800);
  }

  showWaiting();
}

// ── Voice Guard toggle ────────────────────────────────────────────────────────

function setVoiceGuard(enabled) {
  voiceGuardEnabled = enabled;
  const track = document.getElementById("vgTrack");
  const thumb = document.getElementById("vgThumb");
  track.style.background = enabled ? "var(--accent-green)" : "var(--border)";
  thumb.style.transform  = enabled ? "translateX(16px)" : "translateX(0)";
  thumb.style.background = enabled ? "#fff" : "var(--text-muted)";
}

function toggleVoiceGuard() {
  setVoiceGuard(!voiceGuardEnabled);
}

// ── State helpers ─────────────────────────────────────────────────────────────

function showWaiting() {
  document.getElementById("resultWaiting").style.display    = "flex";
  document.getElementById("resultProcessing").style.display = "none";
  document.getElementById("resultContent").style.display    = "none";
}

function showProcessing(label) {
  document.getElementById("resultWaiting").style.display    = "none";
  document.getElementById("resultProcessing").style.display = "flex";
  document.getElementById("resultContent").style.display    = "none";
  document.getElementById("processingLabel").textContent    = label || "Running CostPilot pipeline...";
}

function showResult(html) {
  document.getElementById("resultWaiting").style.display    = "none";
  document.getElementById("resultProcessing").style.display = "none";
  const el = document.getElementById("resultContent");
  el.style.display = "block";
  el.innerHTML     = html;
}

// ── Main submit ───────────────────────────────────────────────────────────────

async function submitCase() {
  const subject = document.getElementById("caseSubject").value.trim();
  const body    = document.getElementById("caseBody").value.trim();
  const dept    = document.getElementById("deptSelect").value;
  const agent   = document.getElementById("agentName").value.trim() || "CostPilot-Demo-Bot";

  if (!subject && !body) {
    document.getElementById("caseBody").focus();
    return;
  }

  const text = subject && body ? subject + "\n\n" + body : subject || body;

  const btn = document.getElementById("submitBtn");
  btn.disabled = true;
  document.getElementById("submitBtnText").textContent = "Routing...";

  const t0 = Date.now();

  try {
    // ── Step 1: Voice Guard (if enabled) ─────────────────────────────────────
    let cleanText            = text;
    let voiceGuardResult     = null;
    let voiceGuardProcessed  = false;

    if (voiceGuardEnabled) {
      showProcessing("🎙 Running Voice Guard — scanning for PII...");
      try {
        voiceGuardResult    = await apiPost("/api/voice/transcript", {
          transcript: text,
          platform:   selectedPlatform,
          department: dept,
        });
        cleanText           = voiceGuardResult.clean_transcript || text;
        voiceGuardProcessed = true;
      } catch (e) {
        // Voice Guard failed — route original text without skip_pii
        voiceGuardResult   = null;
        voiceGuardProcessed = false;
      }
    }

    // ── Step 2: Route ─────────────────────────────────────────────────────────
    showProcessing("◈ Scoring complexity and selecting model tier...");

    let routeResult = null;
    let blocked     = false;
    let blockDetail = null;

    try {
      routeResult = await apiPost("/api/route", {
        text:                  cleanText,
        department:            dept,
        auto_prune:            true,
        agent_name:            agent,
        source_platform:       selectedPlatform,
        voice_guard_processed: voiceGuardProcessed,
        is_test:               false,  // demo org — full pipeline, writes to dashboard
      });
    } catch (err) {
      // HTTP 451 = blocked
      if (err.status === 451 || (err.message && err.message.includes("451"))) {
        blocked = true;
        try { blockDetail = JSON.parse(err.message.replace(/^[^{]*/, "")); } catch(e) {}
        if (!blockDetail) {
          blockDetail = { error: "BLOCKED", reason: "Request blocked by sensitive term policy" };
        }
      } else {
        throw err;
      }
    }

    const latencyMs = Date.now() - t0;

    // ── Fetch the most recent audit event ID for deep-link ────────────────────
    try {
      const auditResp = await apiGet("/api/audit?limit=1");
      if (auditResp && auditResp.length > 0) lastAuditEventId = auditResp[0].id;
    } catch (e) { /* not critical */ }

    // ── Render result ─────────────────────────────────────────────────────────
    if (blocked) {
      showResult(renderBlocked(blockDetail, latencyMs, voiceGuardResult));
    } else {
      showResult(renderRouted(routeResult, latencyMs, voiceGuardResult, cleanText, text));
    }

  } catch (err) {
    showResult(`
      <div style="padding:24px;text-align:center;color:var(--accent-red)">
        <div style="font-size:24px;margin-bottom:12px">⚠</div>
        <div style="font-weight:700;margin-bottom:6px">Submission Error</div>
        <div style="font-size:12px;color:var(--text-muted)">${err.message || "Unknown error"}</div>
        <button class="try-again-btn" onclick="showWaiting()">← Try Again</button>
      </div>
    `);
  } finally {
    btn.disabled = false;
    document.getElementById("submitBtnText").textContent = "Submit to CostPilot →";
  }
}

// ── Render routed result ──────────────────────────────────────────────────────

function renderRouted(r, latencyMs, vgResult, cleanText, rawText) {
  const tier     = (r.model_tier || "Scout").toLowerCase();
  const tierName = r.model_tier || "Scout";
  const isEscalated = r.sensitive_term_triggered && r.sensitive_term_action === "escalate";

  const headerClass = isEscalated ? "escalated" : "routed";
  const statusLabel = isEscalated
    ? `<span class="result-status-label yellow">⚠ ESCALATED — ${tierName}</span>`
    : `<span class="result-status-label green">✓ ROUTED — ${tierName}</span>`;

  const costWithout = r.total_cost_without_pruning || r.cost_usd;
  const costWith    = r.cost_usd;
  const saved       = Math.max(0, (costWithout - costWith) + (r.pruning_cost_saved_usd || 0));

  // Pruning
  const rawTok   = (r.input_tokens || 0) + (r.tokens_saved_by_pruning || 0);
  const cleanTok = r.input_tokens || 0;
  const prunePct = rawTok > 0 ? Math.round((r.tokens_saved_by_pruning / rawTok) * 100) : 0;

  // Budget
  const budgetPct   = r.budget_used_pct || 0;
  const budgetState = budgetPct >= 100 ? "throttled" : budgetPct >= 80 ? "warning" : "healthy";
  const budgetColor = budgetPct >= 100 ? "var(--accent-red)" : budgetPct >= 80 ? "var(--accent-yellow)" : "var(--accent-green)";

  // Keywords
  const keywords     = r.matched_keywords || [];
  const termMatches  = r.sensitive_term_matches || [];
  const termAction   = r.sensitive_term_action || "";

  const keywordHtml = keywords.length || termMatches.length ? `
    <div class="result-section">
      <div class="result-section-label">Keywords Detected</div>
      <div class="keyword-chips">
        ${termMatches.map(k => `<span class="keyword-chip ${termAction}">${k}</span>`).join("")}
        ${keywords.filter(k => !termMatches.includes(k)).map(k => `<span class="keyword-chip match">${k}</span>`).join("")}
      </div>
    </div>
  ` : "";

  // Voice Guard section
  const vgHtml = vgResult && vgResult.redactions_count > 0 ? `
    <div class="result-section">
      <div class="result-section-label">🎙 Voice Guard — PII Redacted Before Routing</div>
      <div class="vg-result">
        <div class="vg-result-label">Clean Transcript (${vgResult.redactions_count} redaction${vgResult.redactions_count !== 1 ? "s" : ""})</div>
        <div class="vg-clean-text">${escHtml(cleanText).replace(/\[REDACTED-([^\]]+)\]/g,
          (_, type) => `<span style="background:#2d0a0a;color:#f85149;border:1px solid #5a1a1a;border-radius:3px;padding:1px 5px;font-weight:700;font-size:11px">[REDACTED-${type}]</span>`
        )}</div>
      </div>
    </div>
  ` : "";

  // Routing reason
  const reason = r.routing_reason || r.complexity || "";

  const auditLink = lastAuditEventId
    ? `/?highlight=${lastAuditEventId}#audit`
    : "/";

  return `
    <div class="result-card">
      <div class="result-header ${headerClass}">
        <div class="result-status-icon">${isEscalated ? "⚠" : "✓"}</div>
        <div class="result-status-text">
          ${statusLabel}
          <div class="result-latency">${latencyMs}ms · ${r.model_name || tierName} · ${r.complexity || "ROUTINE"}</div>
        </div>
        <span class="tier-badge ${tier}">${tierName}</span>
      </div>
      <div class="result-body">

        ${vgHtml}

        <div class="result-section">
          <div class="result-section-label">Routing Reason</div>
          <div class="reason-box">${escHtml(reason)}</div>
        </div>

        <div class="stat-row">
          <div class="stat-box">
            <div class="stat-box-label">Total Cost</div>
            <div class="stat-box-value yellow">$${costWith.toFixed(6)}</div>
          </div>
          <div class="stat-box">
            <div class="stat-box-label">Tokens Used</div>
            <div class="stat-box-value">${(r.input_tokens || 0).toLocaleString()} in · ${(r.output_tokens || 0).toLocaleString()} out</div>
          </div>
        </div>

        ${r.was_pruned ? `
        <div class="result-section">
          <div class="result-section-label">Context Pruning — ${prunePct}% stripped</div>
          <div class="prune-track">
            <div class="prune-fill" style="width:${prunePct}%"></div>
          </div>
          <div class="prune-labels">
            <span>${rawTok.toLocaleString()} tokens raw</span>
            <span>${cleanTok.toLocaleString()} tokens sent to model</span>
          </div>
        </div>
        ` : ""}

        <div class="result-section">
          <div class="result-section-label">Cost Comparison</div>
          <div class="savings-bar">
            <div class="savings-row">
              <span class="label">Without CostPilot (all Advisor)</span>
              <span class="value">$${costWithout.toFixed(6)}</span>
            </div>
            <div class="savings-row">
              <span class="label">With CostPilot routing + pruning</span>
              <span class="value">$${costWith.toFixed(6)}</span>
            </div>
            <hr class="savings-divider"/>
            <div class="savings-row">
              <span class="label" style="font-weight:600;color:var(--text-primary)">Saved on this call</span>
              <span class="saved">+$${saved.toFixed(6)}</span>
            </div>
          </div>
        </div>

        ${keywordHtml}

        <div class="result-section">
          <div class="result-section-label">Department Budget — ${r.department || "Support"}</div>
          <div class="budget-track">
            <div class="budget-fill ${budgetState}" style="width:${Math.min(budgetPct,100)}%"></div>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-top:3px">
            <span style="color:${budgetColor};font-weight:600">${budgetPct}% used</span>
            <span>$${(r.budget_remaining_usd || 0).toFixed(2)} remaining</span>
          </div>
        </div>

        <div class="result-actions">
          <a class="result-action-btn primary" href="${auditLink}" target="_blank">
            📋 See in Audit Log →
          </a>
          <a class="result-action-btn" href="/" target="_blank">
            ◈ View Dashboard →
          </a>
        </div>
        <button class="try-again-btn" onclick="showWaiting();clearScenario()">← Submit another case</button>

      </div>
    </div>
  `;
}

// ── Render blocked result ─────────────────────────────────────────────────────

function renderBlocked(detail, latencyMs, vgResult) {
  const reason  = (detail && detail.reason)   || "Sensitive term policy violation";
  const matches = (detail && detail.matches)  || [];
  const cat     = (detail && detail.category) || "policy";

  const auditLink = lastAuditEventId ? `/?highlight=${lastAuditEventId}#audit` : "/";

  return `
    <div class="blocked-card">
      <div class="blocked-icon">🛡</div>
      <div class="blocked-title">REQUEST BLOCKED</div>
      <div class="blocked-detail">
        ${escHtml(reason)}<br/>
        <strong style="color:var(--text-primary)">Category:</strong> ${escHtml(cat)}
      </div>

      ${matches.length ? `
        <div class="keyword-chips" style="justify-content:center">
          ${matches.map(m => `<span class="keyword-chip block">${escHtml(m)}</span>`).join("")}
        </div>
      ` : ""}

      <div class="blocked-zero">
        <div class="blocked-zero-item">$0.000000 cost</div>
        <div class="blocked-zero-item">0 tokens consumed</div>
      </div>

      <div style="font-size:11px;color:var(--text-muted);line-height:1.6;max-width:320px">
        The request was stopped before it reached any AI model. A full audit event has been written to the immutable log with the timestamp, payload, and reason.
      </div>

      <div style="font-size:11px;color:var(--text-muted)">${latencyMs}ms</div>

      <div class="result-actions" style="width:100%">
        <a class="result-action-btn primary" href="${auditLink}" target="_blank">
          📋 See in Audit Log →
        </a>
        <a class="result-action-btn" href="/" target="_blank">
          ◈ View Dashboard →
        </a>
      </div>
      <button class="try-again-btn" onclick="showWaiting();clearScenario()">← Submit another case</button>
    </div>
  `;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function clearScenario() {
  activeScenario = null;
  document.querySelectorAll(".scenario-btn").forEach(el => el.classList.remove("active"));
}

function escHtml(str) {
  return String(str || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

// ── System status ─────────────────────────────────────────────────────────────

async function initStatus() {
  try {
    const cfg = await apiGet("/api/config");
    const dot   = document.querySelector(".status-dot");
    const label = document.getElementById("statusLabel");
    const badge = document.getElementById("modeBadge");
    dot.className   = "status-dot online";
    label.textContent = "CostPilot Online";
    badge.style.display = "inline-block";
    badge.className     = `mode-badge ${cfg.mode === "live" ? "live" : "simulated"}`;
    badge.textContent   = cfg.mode === "live" ? `Live · ${cfg.provider}` : "Simulated";
  } catch (e) {
    document.getElementById("statusLabel").textContent = "Backend offline";
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
initStatus();
