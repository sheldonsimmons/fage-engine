(() => {
  const personaNames = [
    ["Maya Chen", "maya.chen@example.com"],
    ["Jordan Brooks", "jordan.brooks@example.com"],
    ["Elena Martinez", "elena.martinez@example.com"],
    ["Noah Williams", "noah.williams@example.com"],
    ["Marcus Reed", "marcus.reed@example.com"],
    ["Priya Shah", "priya.shah@example.com"],
    ["Alex Morgan", "alex.morgan@example.com"],
    ["Olivia Bennett", "olivia.bennett@example.com"],
    ["David Kim", "david.kim@example.com"],
    ["Avery Johnson", "avery.johnson@example.com"],
  ];

  const roleByDepartment = {
    Support: ["Support Manager", "Customer Support Specialist"],
    Sales: ["Account Executive", "Sales Operations Analyst"],
    Operations: ["Operations Manager", "Business Operations Analyst"],
    Finance: ["Finance Analyst", "Billing Operations Manager"],
    Marketing: ["Marketing Manager", "Content Operations Specialist"],
    Engineering: ["Engineering Manager", "Quality Systems Analyst"],
    Legal: ["Legal Operations Manager", "Contract Analyst"],
  };

  function createProfile(config) {
    let personIndex = 0;
    const people = [];
    Object.entries(config.departments).forEach(([department, agents]) => {
      agents.forEach((agent, agentIndex) => {
        const [name, email] = personaNames[personIndex % personaNames.length];
        const roles = roleByDepartment[department] || ["Business Manager", "Business Analyst"];
        people.push({
          externalId: `${config.workspaceId}-USER-${String(personIndex + 1).padStart(3, "0")}`,
          name,
          email,
          role: roles[agentIndex % roles.length],
          department,
          team: config.teams[department] || department,
          agent,
          platform: config.platformsByDepartment?.[department]?.[agentIndex]
            || config.platforms[personIndex % config.platforms.length],
        });
        personIndex += 1;
      });
    });
    return { ...config, people };
  }

  const profileMap = {
    enterpriseSaas: createProfile({
      label: "Enterprise SaaS",
      workspaceId: "SIM-ENTERPRISE-SAAS",
      platforms: ["Salesforce Agentforce", "Salesforce Service Cloud", "Zendesk", "Slack"],
      departments: {
        Support: ["Support Resolution Agent", "Case Triage Agent"],
        Sales: ["Renewal Assist Agent", "Pipeline Follow-up Agent"],
        Operations: ["Workflow Ops Agent", "Integration Monitor Agent"],
        Finance: ["Invoice Review Agent", "Spend Review Agent"],
        Marketing: ["Campaign Brief Agent", "Content QA Agent"],
      },
      teams: {
        Support: "Customer Support",
        Sales: "Enterprise Sales",
        Operations: "Business Operations",
        Finance: "Financial Operations",
        Marketing: "Demand Generation",
      },
      platformsByDepartment: {
        Support: ["Salesforce Service Cloud", "Zendesk"],
        Sales: ["Salesforce Agentforce", "Slack"],
        Operations: ["Slack", "Salesforce Agentforce"],
        Finance: ["Salesforce Agentforce", "Slack"],
        Marketing: ["Slack", "Salesforce Agentforce"],
      },
      contexts: [
        { id: "ACME-FY27", type: "Opportunity", account: "Acme Corporation", name: "FY27 Renewal", department: "Sales" },
        { id: "NORTHSTAR-ESC", type: "Case", account: "Northstar Health", name: "Support Escalation", department: "Support" },
        { id: "MERIDIAN-EXP", type: "Account", account: "Meridian Retail", name: "Expansion Plan", department: "Sales" },
        { id: "BLUEPEAK-INT", type: "Project", account: "BluePeak Consulting", name: "Integration Rollout", department: "Operations" },
        { id: "HORIZON-INV", type: "Invoice", account: "Horizon Manufacturing", name: "Invoice Review", department: "Finance" },
        { id: "SUMMIT-CAM", type: "Campaign", account: "Summit Financial", name: "Customer Campaign", department: "Marketing" },
      ],
    }),
    retailServices: createProfile({
      label: "Retail Services",
      workspaceId: "SIM-RETAIL-SERVICES",
      platforms: ["Salesforce Commerce", "Zendesk", "Shopify", "ServiceNow"],
      departments: {
        Support: ["Returns Agent", "Customer Care Agent"],
        Sales: ["Loyalty Offer Agent", "Store Assist Agent"],
        Operations: ["Fulfillment Agent", "Inventory Agent"],
        Finance: ["Refund Review Agent", "Chargeback Agent"],
        Marketing: ["Promotion Agent", "Customer Segment Agent"],
      },
      teams: {
        Support: "Customer Care",
        Sales: "Retail Sales",
        Operations: "Store Operations",
        Finance: "Retail Finance",
        Marketing: "Customer Marketing",
      },
      platformsByDepartment: {
        Support: ["Zendesk", "Salesforce Commerce"],
        Sales: ["Salesforce Commerce", "Shopify"],
        Operations: ["ServiceNow", "Shopify"],
        Finance: ["Salesforce Commerce", "ServiceNow"],
        Marketing: ["Salesforce Commerce", "Shopify"],
      },
      contexts: [
        { id: "STORE-1042", type: "Store", account: "Downtown Flagship", name: "Store Operations", department: "Operations" },
        { id: "ORDER-88219", type: "Order", account: "Jordan Customer", name: "Return and Refund", department: "Support" },
        { id: "LOYALTY-Q3", type: "Campaign", account: "Loyalty Members", name: "Q3 Loyalty Offer", department: "Marketing" },
        { id: "INVENTORY-WEST", type: "Inventory", account: "West Region", name: "Inventory Reconciliation", department: "Operations" },
        { id: "CHARGEBACK-771", type: "Dispute", account: "Online Store", name: "Chargeback Review", department: "Finance" },
        { id: "EXPANSION-NORTH", type: "Account", account: "North Region", name: "Store Expansion", department: "Sales" },
      ],
    }),
    manufacturing: createProfile({
      label: "Manufacturing",
      workspaceId: "SIM-MANUFACTURING",
      platforms: ["ServiceNow", "IoT Ops", "SAP", "Salesforce Field Service"],
      departments: {
        Support: ["Warranty Agent", "Field Support Agent"],
        Sales: ["Distributor Quote Agent", "Renewal Quote Agent"],
        Operations: ["Maintenance Planning Agent", "Production Schedule Agent"],
        Finance: ["Vendor Invoice Agent", "Capex Review Agent"],
        Engineering: ["Quality Review Agent", "Sensor Summary Agent"],
      },
      teams: {
        Support: "Field Service",
        Sales: "Channel Sales",
        Operations: "Plant Operations",
        Finance: "Manufacturing Finance",
        Engineering: "Quality Engineering",
      },
      platformsByDepartment: {
        Support: ["Salesforce Field Service", "ServiceNow"],
        Sales: ["Salesforce Field Service", "SAP"],
        Operations: ["ServiceNow", "SAP"],
        Finance: ["SAP", "ServiceNow"],
        Engineering: ["SAP", "IoT Ops"],
      },
      contexts: [
        { id: "PLANT-7-MAINT", type: "change_request", account: "Plant 7", name: "Equipment Maintenance", department: "Operations" },
        { id: "DIST-QUOTE-84", type: "Quote", account: "Central Distribution", name: "Distributor Quote", department: "Sales" },
        { id: "WARRANTY-552", type: "Case", account: "Apex Industrial", name: "Warranty Claim", department: "Support" },
        { id: "QUALITY-LINE4", type: "Quality Review", account: "Production Line 4", name: "Defect Review", department: "Engineering" },
        { id: "VENDOR-INV-61", type: "Invoice", account: "Titan Components", name: "Vendor Invoice", department: "Finance" },
        { id: "SENSOR-PRESS9", type: "Asset", account: "Press 9", name: "Sensor Analysis", department: "Engineering" },
      ],
    }),
    professionalServices: createProfile({
      label: "Professional Services",
      workspaceId: "SIM-PROFESSIONAL-SERVICES",
      platforms: ["Salesforce", "HubSpot", "Microsoft Teams", "NetSuite"],
      departments: {
        Support: ["Client Request Agent", "Delivery Desk Agent"],
        Sales: ["Proposal Agent", "Account Planning Agent"],
        Operations: ["Staffing Agent", "Project Risk Agent"],
        Finance: ["Billing Agent", "Revenue Forecast Agent"],
        Legal: ["Contract Intake Agent", "Engagement Terms Agent"],
      },
      teams: {
        Support: "Client Success",
        Sales: "Business Development",
        Operations: "Engagement Operations",
        Finance: "Engagement Finance",
        Legal: "Legal Operations",
      },
      platformsByDepartment: {
        Support: ["Microsoft Teams", "Salesforce"],
        Sales: ["Salesforce", "HubSpot"],
        Operations: ["Microsoft Teams", "Salesforce"],
        Finance: ["NetSuite", "Salesforce"],
        Legal: ["Salesforce", "Microsoft Teams"],
      },
      contexts: [
        { id: "ACME-TRANSFORM", type: "Engagement", account: "Acme Corporation", name: "Transformation Program", department: "Operations" },
        { id: "NORTHSTAR-AUDIT", type: "Matter", account: "Northstar Health", name: "Compliance Review", department: "Legal" },
        { id: "MERIDIAN-PROP", type: "Opportunity", account: "Meridian Retail", name: "Advisory Proposal", department: "Sales" },
        { id: "BLUEPEAK-BILL", type: "Invoice", account: "BluePeak Consulting", name: "Billing Review", department: "Finance" },
        { id: "HORIZON-DELIV", type: "Project", account: "Horizon Manufacturing", name: "Delivery Plan", department: "Support" },
        { id: "SUMMIT-STAFF", type: "Project", account: "Summit Financial", name: "Staffing Plan", department: "Operations" },
      ],
    }),
  };

  const styleLabels = {
    typical: "Typical Business Day",
    savings: "Savings and Pruning",
    governance: "Governance Test",
    scale: "Scale Test",
  };

  const scenarioCounts = {
    typical: {
      25: { routine: 18, pruning: 4, moderate: 1, risk: 1, blocked: 1 },
      50: { routine: 36, pruning: 8, moderate: 4, risk: 1, blocked: 1 },
      100: { routine: 72, pruning: 16, moderate: 8, risk: 2, blocked: 2 },
    },
    savings: {
      25: { routine: 18, pruning: 6, moderate: 1, risk: 0, blocked: 0 },
      50: { routine: 36, pruning: 12, moderate: 2, risk: 0, blocked: 0 },
      100: { routine: 72, pruning: 24, moderate: 4, risk: 0, blocked: 0 },
    },
    governance: {
      25: { routine: 15, pruning: 4, moderate: 3, risk: 2, blocked: 1 },
      50: { routine: 30, pruning: 8, moderate: 6, risk: 4, blocked: 2 },
      100: { routine: 60, pruning: 16, moderate: 12, risk: 8, blocked: 4 },
    },
    scale: {
      25: { routine: 20, pruning: 3, moderate: 2, risk: 0, blocked: 0 },
      50: { routine: 40, pruning: 6, moderate: 4, risk: 0, blocked: 0 },
      100: { routine: 80, pruning: 12, moderate: 8, risk: 0, blocked: 0 },
    },
  };

  const subjects = {
    routine: [
      "Update customer record notes",
      "Summarize support conversation",
      "Draft routine follow-up",
      "Classify renewal request",
    ],
    moderate: [
      "Prepare account summary",
      "Review quote request",
      "Summarize incident history",
      "Create customer response plan",
    ],
    pruning: [
      "Clean long customer email thread",
      "Summarize repeated case history",
      "Review forwarded renewal chain",
      "Extract action items from noisy thread",
    ],
    risk: [
      "Review contract language",
      "Summarize confidential vendor concern",
      "Assess legal escalation request",
      "Review liability question",
    ],
    blocked: [
      "Payment verification request",
      "Identity data in support case",
      "Banking details in refund request",
      "Sensitive account recovery note",
    ],
  };

  const state = {
    size: 50,
    style: "typical",
    running: false,
    stopRequested: false,
    totals: null,
  };

  const $ = id => document.getElementById(id);
  const moneyFmt = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  });
  const intFmt = new Intl.NumberFormat("en-US");

  function resetTotals() {
    state.totals = {
      completed: 0,
      sent: 0,
      success: 0,
      blocked: 0,
      scout: 0,
      analyst: 0,
      advisor: 0,
      strategist: 0,
      pruned: 0,
      tokensSaved: 0,
      spend: 0,
      errors: 0,
    };
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function normalizedTier(value) {
    const tier = String(value || "").toLowerCase();
    if (tier.includes("scout") || tier.includes("micro")) return "scout";
    if (tier.includes("analyst")) return "analyst";
    if (tier.includes("advisor") || tier.includes("flagship")) return "advisor";
    if (tier.includes("strategist")) return "strategist";
    if (tier.includes("none")) return "blocked";
    return "scout";
  }

  function deterministicShuffle(values, seed) {
    const result = [...values];
    let value = seed || 1;
    for (let index = result.length - 1; index > 0; index -= 1) {
      value = (value * 9301 + 49297) % 233280;
      const swapIndex = Math.floor((value / 233280) * (index + 1));
      [result[index], result[swapIndex]] = [result[swapIndex], result[index]];
    }
    return result;
  }

  function buildScenarioTypes() {
    const modeCounts = scenarioCounts[state.style] || scenarioCounts.typical;
    const counts = { ...(modeCounts[state.size] || modeCounts[50]) };
    const includeBlocked = $("includeBlockedTest")?.checked !== false;
    if (!includeBlocked && counts.blocked) {
      counts.routine += counts.blocked;
      counts.blocked = 0;
    }

    const types = [];
    ["routine", "pruning", "moderate", "risk", "blocked"].forEach(type => {
      for (let index = 0; index < (counts[type] || 0); index += 1) {
        types.push(type);
      }
    });
    return deterministicShuffle(types, state.size + state.style.length * 31);
  }

  function buildBusinessContext(profile, index) {
    const person = profile.people[index % profile.people.length];
    const matchingContexts = profile.contexts.filter(item => item.department === person.department);
    const availableContexts = matchingContexts.length ? matchingContexts : profile.contexts;
    const work = availableContexts[Math.floor(index / profile.people.length) % availableContexts.length];
    return {
      department: person.department,
      team: person.team,
      agent: person.agent,
      platform: person.platform,
      person,
      work,
      index: index + 1,
    };
  }

  function buildRoutine({ department }) {
    return [
      "Customer request:",
      `Department: ${department}`,
      "Task: Draft a short, friendly status update confirming that the request was received.",
      "Keep the answer brief. No policy exception requested. No contract review requested.",
    ].join("\n");
  }

  function buildModerate({ department }) {
    return [
      `A ${department} team member needs a customer-ready summary and next-step recommendation.`,
      "Review the open notes, summarize the customer's goal, identify missing information, and recommend a response plan.",
      "The customer asked for timing, ownership, dependencies, and whether an internal review is needed before the team replies.",
      "Keep the answer structured with summary, next steps, owner, and customer response.",
    ].join("\n");
  }

  const pruningTemplates = [
    ["Forwarded renewal thread", "email", "Summarize the renewal ask, open questions, and next customer response."],
    ["Support ticket history dump", "ticket", "Extract the current customer problem and next troubleshooting step."],
    ["Slack incident recap", "chat", "Summarize the incident status and identify the owner for follow-up."],
    ["Teams meeting notes", "meeting", "Turn the discussion into a short action list and customer update."],
    ["Invoice OCR paste", "document", "Find the invoice issue and prepare a finance-ready summary."],
    ["Procurement RFQ chain", "email", "Summarize supplier asks and next procurement action."],
    ["CRM activity timeline", "crm", "Summarize the latest useful activity and ignore duplicate history."],
    ["Customer success handoff", "ticket", "Create a handoff summary for the next team."],
    ["Product feedback export", "chat", "Group the feedback into themes and recommended actions."],
    ["Weekly operations digest", "meeting", "Extract only decisions, risks, and owners."],
    ["Sales quote revision thread", "email", "Summarize the quote change request and next approval step."],
    ["Returns case transcript", "ticket", "Identify the active return request and customer-facing response."],
    ["IoT alert log", "log", "Summarize the current equipment issue and urgency."],
    ["Marketing brief revisions", "document", "Extract final campaign changes and unresolved questions."],
    ["Vendor onboarding packet", "document", "Summarize required vendor setup steps."],
    ["Account planning notes", "crm", "Summarize account priorities and next sales motion."],
    ["Customer escalation recap", "email", "Summarize the escalation, owner, and latest commitment."],
    ["Refund review thread", "ticket", "Identify refund reason, status, and recommended next action."],
    ["Field service visit notes", "crm", "Summarize the visit outcome and follow-up needed."],
    ["Internal approval chain", "email", "Extract the approval request and outstanding decision."],
    ["Integration monitor alerts", "log", "Summarize the latest integration failure and likely owner."],
    ["Knowledge article cleanup", "document", "Extract the useful instructions and remove outdated notes."],
    ["Renewal forecast notes", "crm", "Summarize renewal status, risk, and next step."],
    ["Training feedback dump", "chat", "Group employee feedback into practical improvements."],
    ["Customer survey export", "document", "Summarize sentiment and repeated customer issues."],
    ["Service desk macro chain", "ticket", "Identify the real customer ask beneath repeated macros."],
    ["Partner channel update", "email", "Summarize the partner request and internal response needed."],
    ["Shipment exception log", "log", "Summarize the shipment issue and next operations step."],
    ["Budget review notes", "meeting", "Extract spend concerns and recommended action items."],
    ["Campaign launch checklist", "document", "Summarize blockers and launch readiness."],
    ["Case merge history", "ticket", "Find the current case status and ignore merged duplicates."],
    ["Salesforce field dump", "crm", "Summarize populated fields and ignore blanks."],
    ["Zendesk conversation export", "ticket", "Create a customer-facing support summary."],
    ["ServiceNow incident notes", "ticket", "Summarize the active incident and owner."],
    ["HubSpot email sequence", "email", "Extract the latest prospect ask and next response."],
    ["NetSuite invoice notes", "document", "Summarize billing discrepancy and next finance action."],
    ["SAP production notes", "log", "Summarize production delay and operational risk."],
    ["Quality review transcript", "meeting", "Extract defect summary and follow-up actions."],
    ["Maintenance ticket chain", "ticket", "Summarize equipment issue and next maintenance step."],
    ["Distributor quote email", "email", "Summarize quote requirements and missing details."],
    ["Client onboarding notes", "crm", "Summarize onboarding state and next client task."],
    ["Project risk thread", "email", "Summarize project risk and next mitigation step."],
    ["Billing dispute notes", "ticket", "Summarize dispute reason and next finance response."],
    ["Revenue forecast paste", "document", "Extract forecast change and assumptions."],
    ["Contract intake notes", "crm", "Summarize business terms and review owner."],
    ["Engagement kickoff transcript", "meeting", "Summarize kickoff decisions and owners."],
    ["Executive meeting transcript", "meeting", "Extract decisions, risks, and owner follow-ups."],
    ["Ops daily standup", "chat", "Summarize blockers and team commitments."],
    ["Customer care email loop", "email", "Find the current customer ask and next response."],
    ["Warranty claim thread", "ticket", "Summarize warranty status and missing information."],
    ["Store assist chat", "chat", "Summarize store request and recommended response."],
    ["Inventory variance log", "log", "Summarize inventory mismatch and likely cause."],
    ["Chargeback evidence packet", "document", "Extract the evidence summary and next action."],
    ["Loyalty offer notes", "crm", "Summarize customer eligibility and response."],
    ["Content QA findings", "document", "Summarize final content issues and fixes."],
    ["Campaign performance thread", "email", "Extract performance issue and recommended change."],
    ["Workflow automation log", "log", "Summarize failed automation and owner."],
    ["Data sync warning digest", "log", "Summarize sync health and open issues."],
    ["Spend review export", "document", "Summarize spend anomaly and next review step."],
    ["Pipeline follow-up chain", "email", "Summarize opportunity status and next touch."],
    ["Customer renewal notes", "crm", "Summarize renewal risk and next action."],
    ["Support shift handover", "ticket", "Create a clean handover summary."],
    ["Case triage export", "ticket", "Classify the case and recommend next queue."],
    ["Refund queue dump", "ticket", "Summarize active refund request."],
    ["Promotion planning thread", "email", "Summarize promotion request and open approvals."],
    ["Customer segment export", "document", "Summarize useful segment insight."],
    ["Asset renewal notes", "crm", "Summarize renewal asset list and next step."],
    ["Sensor summary dump", "log", "Summarize current signal and operational concern."],
    ["Capex review packet", "document", "Summarize investment request and next review item."],
    ["Proposal revision history", "document", "Extract final proposal ask and open edits."],
    ["Staffing request chat", "chat", "Summarize role need, timing, and owner."],
    ["Delivery desk queue", "ticket", "Summarize delivery issue and next response."],
    ["Account health notes", "crm", "Summarize account risk and next customer touch."],
    ["Meeting follow-up archive", "email", "Extract the actual follow-up request."],
    ["Internal routing transcript", "chat", "Find the final owner and current ask."],
  ];

  const noiseBlocks = {
    email: ({ index, agent, platform, department }) => [
      `From: taylor.casey${index}@example.com`,
      `To: ${agent.toLowerCase().replaceAll(" ", ".")}@example.com`,
      "CC: ops.team@example.com; support.queue@example.com; updates@example.com",
      "Date: Wednesday, May 27, 2026, 8:22 AM",
      `Subject: RE: RE: RE: RE: ${department} customer request - follow up needed`,
      "X-Mailer: Microsoft Outlook 16.0",
      "Importance: High",
      `Thread-ID: demo-thread-${index}-FAKE-ONLY`,
      `Platform-Routing: ${platform} > shared inbox > agent queue`,
      "",
    ],
    ticket: ({ index, agent, platform }) => [
      `Ticket-ID: DEMO-${String(index).padStart(5, "0")}`,
      `Assigned-Agent: ${agent}`,
      `Source-System: ${platform}`,
      "Status history: New > Open > Pending > Waiting on customer > Open > Pending > Open",
      "Macro applied: empathy opening. Macro applied: troubleshooting steps. Macro applied: closing note.",
      "Internal tags: copied, merged, copied, duplicate, customer-visible, pending-review",
      "",
    ],
    chat: ({ index, platform }) => [
      `${platform} export channel: #customer-ops-${index}`,
      "[09:01] system: user joined the channel",
      "[09:02] system: bot reminder posted",
      "[09:03] teammate: adding this here for visibility",
      "[09:04] teammate: bumping old context from the prior thread",
      "[09:05] system: integration notice repeated",
      "",
    ],
    meeting: ({ index }) => [
      `Meeting transcript ID: MTG-${index}-DEMO`,
      "Speaker 1: um, okay, let me restate the context from last week.",
      "Speaker 2: yeah, yeah, just adding color before the real ask.",
      "Speaker 3: repeating the old decision so everyone has it in one place.",
      "Auto-caption notice: transcript may contain filler words and repeated speaker labels.",
      "",
    ],
    document: ({ index, platform }) => [
      `Document export: ${platform}-doc-${index}.txt`,
      "Page 1 of 12",
      "Header: Demo company internal working copy",
      "Footer: Generated by synthetic test data. Do not treat as real customer data.",
      "OCR confidence: medium. Duplicate line blocks may appear.",
      "",
    ],
    crm: ({ index, platform }) => [
      `${platform} record export ID: CRM-DEMO-${index}`,
      "Field dump: Owner, Stage, Amount, Next Step, Last Activity, Empty Field, Empty Field, Empty Field",
      "Activity timeline copied from previous owner notes.",
      "Duplicate system note: record viewed by automation.",
      "Duplicate system note: record viewed by automation.",
      "",
    ],
    log: ({ index, platform }) => [
      `${platform} log export batch ${index}`,
      "2026-05-27T08:22:11Z INFO heartbeat ok",
      "2026-05-27T08:22:12Z INFO heartbeat ok",
      "2026-05-27T08:22:13Z WARN retry scheduled",
      "2026-05-27T08:22:14Z INFO metadata copied from previous run",
      "",
    ],
  };

  const fillerBlocks = [
    "Confidentiality footer: This is synthetic demo footer text repeated to test context pruning. ",
    "Routing metadata: copied from earlier system notes and not needed for the answer. ",
    "Blank-line spacer and old signature content retained from the source platform export. ",
    "Previous response boilerplate: thank you for your patience while we review. ",
    "Audit-safe demo disclaimer: no real customer information is included in this payload. ",
    "Historical note: copied forward from a previous conversation and now stale. ",
  ];

  function buildPruning({ department, agent, platform, index }) {
    const [title, kind, request] = pruningTemplates[(index - 1) % pruningTemplates.length];
    const noise = noiseBlocks[kind] || noiseBlocks.email;
    const noisyHeader = noise({ department, agent, platform, index }).join("\n");
    const repeatedBlocks = 4 + (index % 6);
    const filler = Array.from({ length: repeatedBlocks }, (_, i) => {
      const line = fillerBlocks[(index + i) % fillerBlocks.length];
      return [
        `----- Repeated source block ${i + 1} / ${repeatedBlocks} -----`,
        noisyHeader,
        `Old copied context ${i + 1}: ${line.repeat(5 + (index % 4))}`,
        "Prior status: copied from earlier message. Prior owner: copied from earlier message. Prior deadline: copied from earlier message.",
        "Signature: Taylor Casey | Demo Operations | Phone: 555-0100 | This repeated signature is not needed for the AI answer.",
      ].join("\n");
    }).join("\n\n");

    return [
      `${title}`,
      `Current department: ${department}`,
      `Current agent: ${agent}`,
      `Current platform: ${platform}`,
      "",
      "CURRENT BUSINESS REQUEST:",
      request,
      "Keep the answer short, practical, and focused on the latest useful customer or business ask.",
      "",
      "NOISY SOURCE MATERIAL BELOW:",
      filler,
      "",
      "FINAL NOTE: Use only the latest business request above. Ignore duplicate headers, stale routing notes, repeated signatures, footers, blank fields, and copied history.",
    ].join("\n");
  }

  function buildRisk({ department }) {
    return [
      `${department} request for review: customer is asking about contract language, legal responsibility, liability, and confidential renewal terms.`,
      "Summarize the business risk in plain English, identify whether this should be reviewed before response, and recommend an escalation path.",
      "The request references an NDA, a contract exception, liability concerns, and confidential pricing discussion.",
      "Do not make a legal decision. Prepare a business summary for review.",
    ].join("\n");
  }

  function buildBlocked({ department, index }) {
    return [
      `${department} case note: customer pasted sensitive payment and account recovery details into the support conversation.`,
      `Synthetic test card number 4111 1111 1111 ${String(index).padStart(4, "0")}.`,
      "The message also mentions a bank account, routing number, credit card number, and account verification information.",
      "This is fake demo data only, but it should demonstrate blocked payload behavior before any AI model receives it.",
    ].join("\n");
  }

  function buildText(type, context) {
    if (type === "routine") return buildRoutine(context);
    if (type === "moderate") return buildModerate(context);
    if (type === "pruning") return buildPruning(context);
    if (type === "risk") return buildRisk(context);
    return buildBlocked(context);
  }

  function buildPlan() {
    const profile = profileMap[$("companyProfile").value] || profileMap.enterpriseSaas;
    const scenarioTypes = buildScenarioTypes();
    return scenarioTypes.map((type, index) => {
      const context = buildBusinessContext(profile, index);
      const subjectOptions = subjects[type];
      const subject = subjectOptions[index % subjectOptions.length];
      return {
        type,
        subject,
        workspaceId: profile.workspaceId,
        ...context,
        text: `Subject: ${subject}\n\n${buildText(type, context)}`,
      };
    });
  }

  async function postRoute(item) {
    const response = await fetch("/api/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contract_version: "2026-07-26",
        mode: "control",
        source: {
          platform: item.platform,
          workspace_id: item.workspaceId,
          agent_name: item.agent,
          department: item.department,
          agent_department: item.team,
          charged_department: item.department,
        },
        actor: {
          external_id: item.person.externalId,
          name: item.person.name,
          email: item.person.email,
          role: item.person.role,
          status: "active",
          can_use_ai: true,
          department: item.team,
        },
        work: {
          external_id: item.work.id,
          type: item.work.type,
          name: `${item.work.account} — ${item.work.name}`,
          sync_if_missing: true,
          department: item.work.department,
        },
        request: {
          task: item.subject,
          content: item.text,
          payload_type: "text",
          auto_prune: true,
        },
        department: item.department,
        voice_guard_processed: false,
        is_test: false,
        synthetic_simulation: true,
      }),
    });

    const rawText = await response.text();
    let body = {};
    try {
      body = rawText ? JSON.parse(rawText) : {};
    } catch (_) {
      body = { detail: rawText };
    }

    if (!response.ok) {
      return {
        ok: false,
        blocked: response.status === 451 || /block|sensitive/i.test(body.detail || body.message || rawText),
        status: response.status,
        body,
      };
    }

    return { ok: true, blocked: false, status: response.status, body };
  }

  function updateCounters() {
    const t = state.totals;
    const economy = t.scout + t.analyst;
    const progress = state.size > 0 ? Math.round((t.completed / state.size) * 100) : 0;

    $("sentCount").textContent = intFmt.format(t.sent);
    $("targetCount").textContent = `of ${state.size} activity records`;
    $("economyCount").textContent = intFmt.format(economy);
    $("prunedCount").textContent = intFmt.format(t.pruned);
    $("tokensSavedCount").textContent = intFmt.format(t.tokensSaved);
    $("blockedCount").textContent = intFmt.format(t.blocked);
    $("scoutCount").textContent = intFmt.format(t.scout);
    $("analystCount").textContent = intFmt.format(t.analyst);
    $("advisorCount").textContent = intFmt.format(t.advisor);
    $("strategistCount").textContent = intFmt.format(t.strategist);
    $("spendCount").textContent = moneyFmt.format(t.spend);
    $("errorCount").textContent = intFmt.format(t.errors);
    $("progressBar").style.width = `${progress}%`;
    $("progressText").textContent = `${progress}%`;
  }

  function setStatus(label, subtext) {
    $("runStatus").textContent = label;
    $("runStatusSub").textContent = subtext;
  }

  function clearLog() {
    $("activityLog").innerHTML = `
      <div class="empty-state">
        No simulated requests yet. Start a run to send synthetic AI traffic through CostPilot.
      </div>
    `;
  }

  function addLog({ item, result, tier, blocked, error }) {
    const log = $("activityLog");
    const empty = log.querySelector(".empty-state");
    if (empty) empty.remove();

    const body = result?.body || {};
    const cost = Number(body.cost_usd || 0);
    const saved = Number(body.tokens_saved_by_pruning || 0);
    const label = blocked ? "Blocked" : error ? "Error" : (body.model_tier || tier || "Scout");
    const cls = blocked ? "blocked" : error ? "error" : normalizedTier(label);
    const outcome = blocked
      ? (body.detail || "Sensitive payload stopped")
      : error
        ? (body.detail || body.message || "Request failed")
        : `${body.routing_decision || "Routed"}${saved > 0 ? ` - ${intFmt.format(saved)} tokens saved` : ""}`;

    const node = document.createElement("div");
    node.className = `activity-item ${cls}`;
    node.innerHTML = `
      <div class="activity-pill ${cls}">${escapeHtml(label)}</div>
      <div class="activity-main">
        <div class="activity-title">${escapeHtml(item.person.name)} · ${escapeHtml(item.person.role)} · ${escapeHtml(item.subject)}</div>
        <div class="activity-meta">${escapeHtml(item.department)} / ${escapeHtml(item.team)} · ${escapeHtml(item.agent)} · ${escapeHtml(item.platform)}</div>
        <div class="activity-meta">${escapeHtml(item.work.account)} → ${escapeHtml(item.work.name)} · ${escapeHtml(outcome)}</div>
      </div>
      <div class="activity-cost">${blocked || error ? "-" : moneyFmt.format(cost)}</div>
    `;
    log.prepend(node);

    while (log.children.length > 75) {
      log.removeChild(log.lastElementChild);
    }
  }

  async function sendOne(item) {
    const t = state.totals;
    try {
      const result = await postRoute(item);
      t.completed += 1;

      if (result.blocked) {
        t.blocked += 1;
        t.sent += 1;
        addLog({ item, result, blocked: true });
        updateCounters();
        return;
      }

      if (!result.ok) {
        t.errors += 1;
        addLog({ item, result, error: true });
        updateCounters();
        return;
      }

      t.sent += 1;
      t.success += 1;

      const body = result.body;
      const tier = normalizedTier(body.model_tier);
      if (tier === "scout") t.scout += 1;
      if (tier === "analyst") t.analyst += 1;
      if (tier === "advisor") t.advisor += 1;
      if (tier === "strategist") t.strategist += 1;

      const saved = Number(body.tokens_saved_by_pruning || 0);
      if (body.was_pruned && saved > 0) {
        t.pruned += 1;
        t.tokensSaved += saved;
      }
      t.spend += Number(body.cost_usd || 0);
      addLog({ item, result, tier });
      updateCounters();
    } catch (err) {
      t.completed += 1;
      t.errors += 1;
      addLog({ item, result: { body: { detail: err.message } }, error: true });
      updateCounters();
    }
  }

  function setRunning(isRunning) {
    state.running = isRunning;
    $("runSimulationBtn").disabled = isRunning;
    $("stopSimulationBtn").disabled = !isRunning;
    $("companyProfile").disabled = isRunning;
    $("includeBlockedTest").disabled = isRunning;
    document.querySelectorAll(".choice-btn").forEach(btn => {
      btn.disabled = isRunning;
    });
  }

  async function runSimulation() {
    if (state.running) return;

    resetTotals();
    clearLog();
    updateCounters();
    state.stopRequested = false;
    setRunning(true);
    setStatus("Running", `Sending ${state.size} synthetic AI activity records in small waves.`);

    const plan = buildPlan();
    const waveSize = 5;

    for (let start = 0; start < plan.length; start += waveSize) {
      if (state.stopRequested) break;
      const wave = plan.slice(start, start + waveSize);
      setStatus("Running", `Processing activity records ${start + 1}-${Math.min(start + waveSize, plan.length)}.`);
      await Promise.all(wave.map(sendOne));
      if (start + waveSize < plan.length) {
        await delay(550 + Math.floor(Math.random() * 350));
      }
    }

    const stopped = state.stopRequested;
    setRunning(false);
    setStatus(stopped ? "Stopped" : "Complete", stopped ? "Run stopped by user." : "Open dashboard or reports to view the roll-up.");
  }

  function updateChoice(groupId, attr, value) {
    document.querySelectorAll(`#${groupId} .choice-btn`).forEach(btn => {
      btn.classList.toggle("active", btn.dataset[attr] === String(value));
    });
  }

  function updateLabels() {
    const profile = profileMap[$("companyProfile").value] || profileMap.enterpriseSaas;
    $("styleLabel").textContent = styleLabels[state.style] || "Typical Business Day";
    $("profileLabel").textContent = profile.label;
    $("targetCount").textContent = `of ${state.size} activity records`;
  }

  function bindEvents() {
    $("sizeChoices").addEventListener("click", event => {
      const btn = event.target.closest("[data-size]");
      if (!btn || state.running) return;
      state.size = Number(btn.dataset.size || 50);
      updateChoice("sizeChoices", "size", state.size);
      updateLabels();
      updateCounters();
    });

    $("styleChoices").addEventListener("click", event => {
      const btn = event.target.closest("[data-style]");
      if (!btn || state.running) return;
      state.style = btn.dataset.style || "typical";
      updateChoice("styleChoices", "style", state.style);
      updateLabels();
    });

    $("companyProfile").addEventListener("change", updateLabels);
    $("runSimulationBtn").addEventListener("click", runSimulation);
    $("stopSimulationBtn").addEventListener("click", () => {
      state.stopRequested = true;
      $("stopSimulationBtn").disabled = true;
      setStatus("Stopping", "Finishing the current wave.");
    });
    $("clearLogBtn").addEventListener("click", clearLog);
  }

  document.addEventListener("DOMContentLoaded", () => {
    resetTotals();
    bindEvents();
    updateLabels();
    updateCounters();
  });
})();
