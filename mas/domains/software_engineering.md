# Domain Context: Software Engineering

## Purpose of This Domain Context
This document captures the core concepts, engineering standards, risks, and modern practices that define strong software engineering in production environments. It is intended to be used as a reusable domain-expertise reference for design, implementation, review, operations, and governance.

Software engineering is broader than writing code. It includes problem framing, system design, delivery, security, operability, evolution over time, and the socio-technical processes that allow teams to build reliable systems sustainably.

---

## 1. Core Principles

### 1.1 Simplicity and Modularity
- **KISS (Keep It Simple, Stupid):** prefer the simplest design that correctly solves the current problem.
- **YAGNI (You Ain't Gonna Need It):** avoid speculative abstractions and features that are not yet justified by real requirements.
- **Separation of concerns:** isolate business logic, infrastructure concerns, persistence, and user-facing interfaces.
- **High cohesion, low coupling:** modules should have focused responsibilities and minimal hidden dependencies.
- **Explicitness over cleverness:** code should optimize for readability and maintainability, not novelty.

### 1.2 SOLID and Object/Module Design
- **Single Responsibility Principle:** each unit should have one reason to change.
- **Open/Closed Principle:** systems should be extensible without requiring frequent modification of stable code.
- **Liskov Substitution Principle:** implementations must preserve the expectations of the abstraction they implement.
- **Interface Segregation Principle:** consumers should depend only on methods they actually use.
- **Dependency Inversion Principle:** high-level policy should depend on abstractions, not concrete infrastructure details.

These principles apply beyond object-oriented systems. In service-oriented and functional systems, they still map to boundary design, API contracts, dependency structure, and deployable units.

### 1.3 DRY, but Not Over-Abstracted
- **DRY (Don't Repeat Yourself):** keep one authoritative source of truth for business rules, schemas, and workflows.
- Avoid premature abstraction: duplicated code is often less harmful than the wrong abstraction.
- Prefer to remove duplication after patterns become stable and recurring.

### 1.4 Correctness Before Optimization
- Make software correct, observable, and testable before attempting aggressive optimization.
- Optimize only after measurement confirms a meaningful bottleneck.
- Consider algorithmic complexity, I/O patterns, concurrency, caching, and database access before micro-optimizations.

### 1.5 Fail Fast, Recover Gracefully
- Surface invalid states early.
- Validate contracts at system boundaries.
- Prefer loud failures in development and controlled degradation in production.
- Distinguish between recoverable and unrecoverable errors.
- Use retries, timeouts, circuit breakers, backoff, and idempotency where failure is expected in distributed systems.

### 1.6 Engineering as a Lifecycle Discipline
Software must be maintainable after release. Every decision should consider:
- changeability,
- backward compatibility,
- operational cost,
- security impact,
- incident response,
- and developer ergonomics.

---

## 2. What Good Software Engineering Optimizes For
A mature engineering organization does not optimize for feature throughput alone. It balances:
- **Correctness:** the system does what it is supposed to do.
- **Reliability:** it continues to work under normal and adverse conditions.
- **Maintainability:** developers can safely modify it.
- **Security:** it resists abuse and limits blast radius.
- **Performance efficiency:** it meets latency, throughput, and cost goals.
- **Scalability:** it handles growth in load, data, and team size.
- **Usability and accessibility:** interfaces are understandable and inclusive.
- **Operability:** the system can be monitored, debugged, and restored.
- **Resilience:** the system tolerates partial failure and recovers predictably.
- **Compliance and auditability:** important changes and decisions can be traced.

These qualities often trade off against one another. Good engineering makes trade-offs explicit rather than accidental.

---

## 3. Quality Standards

### 3.1 Code Review and Change Management
- All production changes should go through peer review.
- Reviews should check correctness, security, test coverage, migration safety, backward compatibility, observability, and maintainability.
- Large changes should be decomposed into smaller, reviewable units.
- Significant technical decisions should be recorded in **Architecture Decision Records (ADRs)**.

### 3.2 Testing Expectations
Tests are part of the product, not an afterthought.

A healthy testing strategy usually includes:
- **Unit tests:** verify local logic quickly and deterministically.
- **Integration tests:** verify collaboration across components, databases, queues, or external services.
- **Contract tests:** verify expectations between services or between client and API.
- **End-to-end tests:** validate critical user journeys.
- **Non-functional tests:** performance, load, stress, security, accessibility, and disaster-recovery validation.

Guidelines:
- Tests must be reliable and reproducible.
- Flaky tests should be treated as defects.
- Critical bugs should produce regression tests.
- Test data and fixtures should be easy to understand and maintain.

### 3.3 CI/CD as a Quality Gate
A modern CI/CD pipeline should validate:
- formatting and static analysis,
- dependency and license checks,
- unit and integration tests,
- build reproducibility,
- artifact generation,
- container/image scanning,
- migration safety where relevant,
- and deployment policy checks.

Continuous delivery should emphasize:
- small batch sizes,
- automated rollback or roll-forward strategies,
- progressive delivery,
- environment parity,
- and safe release controls.

### 3.4 Documentation Standards
Minimum expected documentation includes:
- public API documentation,
- service ownership,
- ADRs for major decisions,
- onboarding notes,
- operational runbooks,
- incident response procedures,
- data contracts and schemas,
- and change logs or release notes.

Documentation must evolve with the system. Stale documentation can be worse than missing documentation.

### 3.5 Observability Standards
Critical paths should emit:
- **logs** for discrete events and debugging context,
- **metrics** for aggregate health and performance,
- **traces** for request flow across services,
- and **structured context** such as correlation IDs and user-safe request metadata.

Modern observability practice increasingly standardizes on **OpenTelemetry** for generating, collecting, and exporting telemetry across traces, metrics, and logs. citeturn755825search6turn755825search10

### 3.6 Reliability Standards
- Define **SLIs** (what is measured), **SLOs** (what target is expected), and where useful **error budgets**.
- Runbooks must exist for common operational failures.
- Production systems should support safe restart, rollback, and partial degradation.
- Backup and restore procedures should be tested, not assumed.
- Critical dependencies should have timeout, retry, and fallback strategies.

---

## 4. Secure Software Engineering
Security is not a final review step; it is a lifecycle concern.

### 4.1 Secure SDLC
A strong baseline is to align engineering practice with the **NIST Secure Software Development Framework (SSDF)**, which recommends secure practices across preparation, protection of software, secure production, and vulnerability response. citeturn755825search5turn755825search1turn755825search13

Core secure-development expectations:
- threat modeling for material changes,
- secure defaults and least privilege,
- secrets management outside source control,
- code review with security awareness,
- dependency scanning and patch hygiene,
- secure build and release pipelines,
- vulnerability disclosure and remediation processes,
- and auditability for security-relevant actions.

### 4.2 Application Security
The **OWASP Top 10** remains a practical baseline for common web application risks, including broken access control, cryptographic failures, injection, insecure design, security misconfiguration, vulnerable components, identification and authentication failures, software and data integrity failures, logging and monitoring failures, and server-side request forgery. citeturn755825search16turn755825search0

Security reviews are especially important for any changes involving:
- authentication,
- authorization,
- session handling,
- file upload,
- template rendering,
- deserialization,
- payment flows,
- multi-tenant data access,
- and personally identifiable information (PII).

### 4.3 Supply-Chain Security
Modern engineering must assume dependency and build pipelines are attack surfaces.

Good practice includes:
- pinning and reviewing dependencies,
- verifying artifact provenance,
- minimizing build-time trust,
- isolating build environments,
- signing release artifacts where possible,
- and tracking transitive dependencies.

Two modern reference points are:
- **SLSA (Supply-chain Levels for Software Artifacts):** a framework for incrementally improving software supply-chain integrity and trust. citeturn728782search0turn728782search8turn728782search12
- **SBOM (Software Bill of Materials):** an inventory of software components used to improve supply-chain transparency and risk management. citeturn728782search1turn728782search5

### 4.4 Identity and Access
- Prefer centralized identity and standards-based authentication.
- **OAuth 2.0 / OpenID Connect** are standard choices for delegated authorization and identity.
- Apply least privilege to both human users and service accounts.
- Use short-lived credentials when possible.
- Rotate secrets and signing keys.
- Prefer phishing-resistant authentication methods such as passkeys/WebAuthn where feasible.

---

## 5. Architecture and System Design

### 5.1 Architecture as Trade-off Management
Architecture is the set of important decisions about structure, boundaries, communication, and evolution. It should optimize for current needs while preserving room for change.

Important architectural concerns include:
- domain boundaries,
- deployment boundaries,
- data ownership,
- consistency requirements,
- latency sensitivity,
- operational overhead,
- and failure modes.

### 5.2 Monoliths, Modular Monoliths, and Microservices
- A **monolith** can be the best choice when domain complexity is moderate and team coordination is easier with a single deployable unit.
- A **modular monolith** preserves strong internal boundaries without incurring the full cost of distributed systems.
- **Microservices** can improve autonomy and scaling, but add operational complexity, network failure modes, consistency challenges, and observability requirements.

Rule of thumb: do not adopt distributed architecture for organizational fashion. Adopt it when bounded contexts, deployment needs, or scale clearly justify the cost.

### 5.3 Data Ownership and Contracts
- Each service should own its data model and persistence boundaries where feasible.
- Shared databases create tight coupling and brittle change coordination.
- Favor explicit contracts between systems: APIs, schemas, event formats, and versioning rules.
- Contract changes should be backward compatible whenever possible.

### 5.4 API Design
Good APIs should be:
- consistent,
- explicit,
- versioned appropriately,
- secure by default,
- and designed around consumer workflows rather than internal implementation details.

API concerns include:
- idempotency for retried writes,
- pagination and filtering,
- rate limiting,
- stable error formats,
- schema evolution,
- and compatibility guarantees.

REST remains common, but gRPC, GraphQL, event-driven interfaces, and asynchronous workflows are also appropriate depending on latency, coupling, and query flexibility requirements.

### 5.5 Event-Driven and Distributed Systems Patterns
Useful prior art includes:
- **Circuit Breaker:** avoid cascading failures when downstream systems are unhealthy.
- **Bulkhead:** isolate resources so one failing component does not starve others.
- **Saga:** coordinate multi-step workflows without global distributed transactions.
- **CQRS:** separate command and query models when read and write concerns differ.
- **Event Sourcing:** persist state changes as an append-only event log when auditability and reconstruction matter.
- **Outbox pattern:** publish state changes safely alongside database writes.
- **Idempotent consumers:** handle duplicate messages safely.

These patterns are powerful but add complexity; they should be justified by real requirements.

---

## 6. Data and Persistence Engineering

### 6.1 Schema and Migration Safety
- Database schema changes should be backward compatible during rolling deployments.
- Avoid changes that require all services to upgrade simultaneously.
- Expand-and-contract migration strategies are safer than destructive one-step changes.
- Migrations should be tested on production-like data volumes.

### 6.2 Transactionality and Consistency
- Prefer local transactions within bounded contexts.
- In distributed systems, exact global consistency is often expensive or impossible.
- Engineers must understand trade-offs among consistency, availability, and partition tolerance.
- Eventual consistency is acceptable only when the business semantics tolerate it.

### 6.3 Data Durability and Recovery
- Backups must be automated, encrypted where appropriate, and regularly tested.
- Restore time objectives and restore point objectives should be defined for critical systems.
- Idempotent writes and replayable events improve recovery options.

### 6.4 Privacy by Design
- Minimize collection of personal data.
- Define retention and deletion policies.
- Protect sensitive data in transit and at rest.
- Separate confidential data from logs, analytics, and debugging channels.

---

## 7. Delivery, Operations, and Reliability Engineering

### 7.1 Build and Release Management
- Every build should be reproducible and traceable to source revision, dependency set, configuration, and artifact digest.
- Environments should differ by configuration, not by hidden behavior.
- Release procedures should support rollback, roll-forward, and rapid containment.

### 7.2 Progressive Delivery
Prefer staged rollout strategies such as:
- canary releases,
- blue-green deployments,
- shadow traffic,
- and feature-flag-driven enablement.

Feature flags are most useful when they are treated as managed configuration, not permanent branching logic. The OpenFeature project provides a vendor-neutral specification for feature flagging APIs and integrations. citeturn755825search3turn755825search7turn755825search19

### 7.3 Incident Management
A mature incident process includes:
- detection,
- triage,
- communication,
- mitigation,
- recovery,
- root-cause analysis,
- and follow-through on corrective actions.

Blameless postmortems should focus on system conditions, missing safeguards, decision context, and improvements to tooling or process.

### 7.4 Reliability Engineering
Service reliability depends on:
- sane defaults,
- capacity planning,
- graceful degradation,
- dependency isolation,
- load shedding,
- retry control,
- and clear ownership.

Performance-critical paths should be load tested before release. Reliability assumptions should be validated with fault injection, chaos experiments, or controlled failure drills when appropriate.

---

## 8. Common Risks and Failure Modes

### 8.1 Premature Optimization
- Optimizing before measurement can degrade clarity and introduce bugs.
- First profile, then optimize the real bottleneck.

### 8.2 Technical Debt
Technical debt is not simply bad code. It includes unowned architecture, undocumented decisions, brittle tests, risky migrations, poor observability, and manual operations that slow future change.

Debt becomes dangerous when:
- changes grow slower or riskier,
- on-call burden rises,
- defects recur,
- and teams avoid touching critical areas.

### 8.3 Single Points of Failure
SPOFs can exist in:
- infrastructure,
- services,
- teams,
- knowledge distribution,
- deployment processes,
- or external vendors.

Redundancy, automation, documentation, and cross-training are all anti-SPOF measures.

### 8.4 Security and Trust Failures
- insecure defaults,
- over-privileged access,
- exposed secrets,
- weak dependency governance,
- unsigned artifacts,
- and missing audit trails
can turn minor defects into major incidents.

### 8.5 Distributed Systems Complexity
- network partitions,
- message duplication,
- out-of-order delivery,
- clock skew,
- retry storms,
- and partial failure
create bugs that do not exist in single-process systems.

### 8.6 Operational Blindness
If a team cannot answer what changed, where latency increased, which dependency is failing, or which users are affected, the system is under-observed.

### 8.7 Validation Gaps in AI-Assisted Development
AI coding tools can improve velocity, but they increase the need for strong review, testing, security validation, provenance awareness, and license scrutiny. Generated code is not trustworthy by default.

### 8.8 Accessibility and UX Neglect
Software can be technically correct yet unusable. Engineering quality includes accessibility, error clarity, latency perception, localization, and support for degraded conditions.

---

## 9. Best Practices

### 9.1 Source Control and Change Hygiene
- Use version control for all code, infrastructure definitions, and important configuration.
- Keep commits focused and meaningful.
- Prefer trunk-based development or short-lived branches over long-lived divergence.
- Protect main branches with review and CI requirements.

### 9.2 Infrastructure as Code
- Define infrastructure declaratively.
- Review infrastructure changes like application code.
- Avoid undocumented manual production changes.
- Keep environment creation reproducible.

### 9.3 Static Analysis and Automated Safeguards
Use automated checks for:
- formatting,
- linting,
- type checking,
- dead code,
- secret detection,
- dependency vulnerabilities,
- container misconfiguration,
- and policy enforcement.

### 9.4 Backward Compatibility by Default
- APIs, events, and schemas should evolve in compatible ways.
- Consumer-driven contract testing helps reduce accidental breakage.
- Deprecations should be announced, monitored, and removed intentionally.

### 9.5 Design for Operability
- emit structured logs,
- propagate correlation IDs,
- expose health endpoints carefully,
- publish actionable metrics,
- and keep runbooks close to the service.

### 9.6 Use ADRs for Important Decisions
ADRs are especially useful when choosing:
- storage technologies,
- messaging patterns,
- authentication approaches,
- deployment models,
- consistency models,
- or major refactors.

### 9.7 Use Feature Flags Carefully
- Separate deploy from release.
- Support gradual rollout and fast rollback.
- Clean up stale flags; they are temporary control points, not permanent architecture.

### 9.8 Performance Engineering Discipline
- Define latency and throughput goals.
- Measure p50/p95/p99 behavior, not only averages.
- Consider resource efficiency and cloud cost alongside speed.
- Benchmark under realistic concurrency and dataset sizes.

### 9.9 Twelve-Factor and Cloud-Native Thinking
The **Twelve-Factor App** remains influential for cloud-native service design, especially around config, disposability, stateless processes, logs, and environment parity, though teams should adapt it to modern container and platform realities rather than apply it mechanically. citeturn728782search3turn728782search7turn728782search15turn728782search19

---

## 10. Prior Art and Reusable Patterns

### 10.1 Classical Design Patterns
Useful object and module patterns include:
- Factory,
- Strategy,
- Adapter,
- Observer,
- Decorator,
- Command,
- Template Method,
- Repository,
- Dependency Injection.

Patterns are tools, not goals. Overuse can make systems harder to understand.

### 10.2 Architectural Styles
Common styles include:
- layered architecture,
- hexagonal / ports-and-adapters,
- clean architecture,
- event-driven architecture,
- microkernel / plugin architecture,
- service-oriented architecture,
- and data-pipeline-oriented systems.

Each style carries a bias about testing, boundaries, and changeability.

### 10.3 Delivery and Team Patterns
- trunk-based development,
- continuous integration,
- platform engineering,
- golden paths and paved roads,
- internal developer platforms,
- on-call rotations,
- and blameless postmortems.

### 10.4 Metrics for Engineering Performance
DORA’s guidance currently emphasizes five software delivery performance metrics: deployment frequency, lead time for changes, change failure rate, failed deployment recovery time, and reliability. These are useful at the system level, but they should not be used as simplistic individual productivity metrics. citeturn728782search2turn728782search6turn728782search18

---

## 11. Modern Extensions to the Original Source
Compared with the original, a more current view of software engineering should explicitly include:
- **Secure SDLC** as a first-class concern, not only generic “security reviews.”
- **Supply-chain security** through provenance, SLSA-style controls, and SBOM generation. citeturn728782search0turn728782search1
- **OpenTelemetry-style observability** instead of ad hoc logging alone. citeturn755825search6
- **Progressive delivery and feature-flag standardization** via vendor-neutral practices such as OpenFeature. citeturn755825search7
- **Reliability engineering** with SLIs, SLOs, error budgets, and incident learning.
- **Platform engineering** and paved-road approaches for developer productivity.
- **API and schema evolution discipline** for backward compatibility.
- **Accessibility, privacy, and compliance** as engineering requirements, not only legal afterthoughts.
- **AI-assisted development governance** for reviewability, provenance, and verification.

---

## 12. Compact Review Checklist
A software change is usually not ready for production unless the team can answer most of the following:
- Is the problem and scope clearly defined?
- Is the design simple enough for current needs?
- Are security, privacy, and abuse cases considered?
- Are tests sufficient and stable?
- Is observability in place for critical paths?
- Are migrations safe and reversible?
- Is rollback or mitigation possible?
- Are contracts backward compatible?
- Is ownership clear?
- Is documentation updated?
- Are dependencies and artifacts trustworthy?
- Is operational load acceptable?

---

## 13. Short Glossary
- **ADR:** Architecture Decision Record.
- **SLO/SLI:** service level objective / indicator used to manage reliability.
- **Idempotency:** repeated execution produces the same intended outcome.
- **Blast radius:** the scope of impact when a component fails.
- **Progressive delivery:** releasing gradually rather than exposing all users at once.
- **SBOM:** inventory of software components used in a system. citeturn728782search1
- **SLSA:** framework for incrementally improving software supply-chain integrity. citeturn728782search0
- **OpenTelemetry:** vendor-neutral observability framework for telemetry signals. citeturn755825search6
- **OpenFeature:** vendor-neutral feature flagging specification. citeturn755825search7

---

## 14. Suggested Condensed Version for Fast Reference
If a shorter operational summary is needed, software engineering can be reduced to the following idea:

> Build systems that are simple enough to change, safe enough to trust, observable enough to operate, and well-governed enough to evolve.

That requires disciplined design, automated validation, secure delivery, production-grade observability, explicit architecture, and continuous attention to reliability, maintainability, and human factors.
