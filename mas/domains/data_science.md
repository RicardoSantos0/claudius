# Domain Context: Data Science

## Purpose and Scope
This document defines domain expertise expectations for data science work across the full lifecycle of analytical and machine learning systems. It covers classical statistics and machine learning, deep learning, generative AI, experimentation, evaluation, MLOps, governance, and deployment. It is intended to support consistent decision-making in research, development, production, and audit contexts.

Data science is treated here as the disciplined combination of:
- **Data**: observations represented as features, labels, events, or unstructured content.
- **Models**: computational mappings from inputs to outputs, probabilities, scores, policies, embeddings, or generated artifacts.
- **Objectives**: loss functions, utility functions, and business constraints used to optimize model behavior.
- **Evaluation**: statistical, operational, ethical, and business criteria used to determine whether a system is fit for use.

The goal is not only high predictive performance, but also reproducibility, reliability, fairness, robustness, interpretability, and operational usefulness.

---

## Core Principles

### 1. Reproducibility
All experiments must be reproducible from code, data, configuration, and environment.
- Fix random seeds for all relevant libraries and frameworks such as NumPy, PyTorch, TensorFlow, and data loaders.
- Record package versions, hardware assumptions, and environment configuration.
- Version datasets, features, training code, and model artifacts.
- Ensure that stochastic procedures such as initialization, shuffling, negative sampling, and mini-batch construction are controlled.
- Store experiment metadata so results can be re-run and audited later.

### 2. Data Integrity
Source data is immutable.
- Raw data must never be overwritten.
- All preprocessing steps must be explicit, versioned, and reviewable.
- Schema, units, timestamps, label definitions, and missing-value conventions must be documented.
- Data lineage should make it possible to trace any feature back to raw sources.

### 3. Leakage Prevention
Train/validation/test boundaries are sacrosanct.
- Data splitting is performed before exploratory transformation or feature selection whenever feasible.
- Parameters for scaling, imputation, encoding, target transformation, dimensionality reduction, and feature selection are learned only from the training partition.
- For temporal data, respect chronology and avoid random shuffling that leaks future information into the past.
- Grouped entities such as patients, users, devices, or households must not be split across train and test when leakage can occur through identity overlap.

### 4. Measurement Discipline
What is optimized must match the real objective.
- Use evaluation metrics aligned with business and scientific goals.
- Accuracy alone is insufficient in imbalanced or high-cost decision settings.
- Offline metrics should be complemented with calibration, fairness, uncertainty, latency, and robustness checks.
- Final claims must be based on a held-out test set or a properly designed online evaluation.

### 5. Scientific Thinking
Models are hypotheses, not truths.
- Begin with explicit assumptions about the data-generating process.
- Compare against simple baselines before introducing complexity.
- Run ablations to understand which components matter.
- Distinguish correlation from causation unless the study design supports causal claims.

### 6. Transparency and Accountability
Data science systems must be understandable enough to govern.
- Document model purpose, intended users, input assumptions, limitations, and known failure modes.
- Maintain model cards, datasheets, or equivalent governance artifacts.
- In regulated or high-stakes settings, prioritize explainability, auditability, privacy, and contestability.

### 7. Inductive Bias Awareness
Every model architecture imposes assumptions.
- Linear models assume additive relationships.
- Trees assume hierarchical partitioning.
- CNNs assume locality and translation-related structure.
- RNNs and Transformers assume sequence structure, but with different mechanisms for dependency modeling.
- Graph neural networks assume relational structure represented by nodes and edges.
These assumptions should be matched to the problem domain and explicitly documented.

---

## The Triad of Machine Learning
Any machine learning system can be analyzed through three core components:

### Data
Data consists of examples described by features and, in supervised settings, labels.
- Features may be numerical, categorical, temporal, spatial, graph-structured, textual, visual, or multimodal.
- Labels may be classes, real values, sequences, rankings, preferences, or future outcomes.
- Data quality is often the main determinant of performance.

### Model
A model defines the hypothesis space.
- In classical ML, this may be a linear separator, tree ensemble, kernel method, or probabilistic model.
- In DL, this may be an MLP, CNN, RNN, Transformer, diffusion model, or GNN.
- Model capacity controls how complex a function the model can represent.

### Loss Function
The loss function expresses what it means to be wrong.
- Classification often uses cross-entropy.
- Regression commonly uses squared error or absolute error.
- Ranking, contrastive learning, metric learning, and reinforcement learning use task-specific objectives.
- In production, optimization may also include constraints such as fairness, latency, memory, and energy efficiency.

---

## Mathematical and Statistical Foundations

### Probability and Uncertainty
A solid data science practice requires probability theory.
- Distinguish random variables, distributions, expectation, variance, covariance, and conditional probability.
- Understand likelihood, posterior, prior, and evidence in Bayesian settings.
- Separate **aleatoric uncertainty** (inherent noise) from **epistemic uncertainty** (lack of knowledge or data).
- Use predictive intervals, calibration, ensembling, Bayesian approximations, or conformal methods when uncertainty matters.

### Linear Algebra
Modern ML relies on vector and matrix representations.
- Features are often represented as vectors in high-dimensional spaces.
- Embeddings map discrete objects into continuous vector spaces.
- Matrix multiplication, eigenvalues, singular value decomposition, and tensor operations are foundational.

### Calculus and Optimization
Training requires optimization over differentiable or piecewise differentiable objectives.
- Gradients indicate local directions of steepest increase.
- Backpropagation applies the chain rule to compute gradients efficiently.
- First-order optimizers include SGD, momentum, RMSProp, and Adam.
- Optimization quality depends on learning rate schedules, normalization, initialization, and batch design.

### Statistical Learning Theory
The core concern is **generalization**.
- **Empirical Risk Minimization (ERM)** minimizes average loss on observed data.
- Good test performance depends on how well learned patterns transfer to unseen data.
- Classical theory highlights the **bias-variance trade-off**: overly simple models underfit, overly flexible models overfit.
- In high dimensions, sample complexity and the **curse of dimensionality** become major concerns.

### Double Descent
Classical intuition suggests a U-shaped relationship between model complexity and test error. However, modern overparameterized models can exhibit **double descent**:
1. Error initially decreases as complexity grows.
2. Error spikes near the interpolation threshold, where the model is just flexible enough to fit the training data exactly.
3. Error can decrease again as complexity increases further beyond that threshold.

Implications:
- More parameters do not always imply worse generalization.
- Regularization, implicit bias of optimization, data augmentation, and early stopping influence where the peak occurs.
- Double descent is especially relevant in deep learning and large-scale models, where interpolation is common.

---

## Data Lifecycle Standards

### Problem Framing
Before modeling, define:
- decision to support
- prediction target
- unit of analysis
- prediction horizon
- acceptable error types
- deployment context
- fairness and compliance constraints

Poor framing causes many failures long before modeling begins.

### Data Collection and Labeling
- Ensure labels correspond to the real operational target, not a noisy proxy when avoidable.
- Document how labels were created, by whom, and under what instructions.
- Quantify inter-annotator agreement when human labeling is involved.
- Be cautious with weak labels, synthetic labels, and delayed outcomes.

### Splitting Strategy
Train/validation/test split is established early and defended rigorously.
- Standard i.i.d. splits are acceptable for exchangeable observations.
- Time series requires chronological splits.
- Repeated entities require group-aware splits.
- Data-rich settings may use 50/25/25 or 60/20/20 splits.
- Smaller datasets often benefit from cross-validation plus a final held-out test set.

### Missing Data
Missingness must be treated as a first-class problem.
- Determine whether data is missing completely at random, at random, or not at random.
- Common strategies include deletion, mean or median imputation, model-based imputation, or missingness indicators.
- Imputation parameters must be learned from training data only.
- Missing data can itself be informative and should sometimes be modeled explicitly.

### Data Quality Checks
Implement checks for:
- schema validity
- type consistency
- out-of-range values
- impossible timestamps
- duplicate records
- label drift
- feature freshness
- null-rate changes
- training-serving skew

---

## Preprocessing and Feature Engineering

### General Rules
- Preprocessing must be encapsulated in a pipeline.
- Never fit preprocessing on validation or test data.
- Keep a clear distinction between raw features, engineered features, and target-derived information.

### Numerical Features
- Standardization to zero mean and unit variance is often beneficial for gradient-based models and distance-based methods.
- Robust scaling may be preferable with heavy outliers.
- Log transforms can stabilize skewed positive variables.

### Categorical Features
- Do not encode nominal variables as arbitrary integers when the model might interpret them as ordinal.
- Use one-hot encoding, target encoding with leakage controls, learned embeddings, or hashing where appropriate.
- Track unseen categories at inference time.

### Text Features
- Classical methods include bag-of-words, TF-IDF, and topic models.
- Neural methods include learned embeddings, contextual embeddings, and Transformer tokenization pipelines.
- Preprocessing decisions such as stemming, lemmatization, lowercasing, and subword tokenization must be aligned with the model class.

### Image and Signal Features
- Standard steps include resizing, normalization, augmentation, and channel handling.
- Data augmentation should preserve task semantics.
- Leakage can occur if near-duplicate images appear across splits.

### Representation Learning
Feature engineering increasingly includes learned representations.
- Autoencoders, contrastive learning, self-supervised learning, and foundation-model embeddings are used to obtain transferable features.
- Learned representations must still be evaluated for drift, fairness, and downstream suitability.

---

## Modeling Workflow

### Baselines First
Every project should establish a baseline before building complex systems.
- Classification baselines: majority class, logistic regression, Gaussian Naive Bayes, linear SVM, tree ensembles.
- Regression baselines: mean predictor, linear regression, ridge or lasso, gradient boosting.
- Time-series baselines: last value, seasonal naïve, exponential smoothing.
- NLP baselines: TF-IDF plus linear classifier before Transformers.

A baseline defines the minimum bar for added complexity.

### Model Families

#### Linear and Generalized Linear Models
Useful for interpretability, robustness, and strong tabular baselines.
- linear regression
- logistic regression
- ridge, lasso, elastic net
- Poisson and other GLMs

#### Distance-Based and Kernel Methods
- k-nearest neighbors
- support vector machines
- kernel ridge and related methods

#### Tree-Based Methods
Often state-of-the-art for tabular data.
- decision trees
- random forests
- gradient boosted trees such as XGBoost, LightGBM, and CatBoost

#### Probabilistic Models
Useful when uncertainty and explicit assumptions matter.
- naive Bayes
- hidden Markov models
- Gaussian processes
- Bayesian regression and hierarchical models

#### Neural Networks
Used when representation learning or large-scale nonlinear modeling is required.
- multilayer perceptrons
- CNNs for vision and local patterns
- RNNs, LSTMs, and GRUs for sequences
- Transformers for language, multimodal, and general sequence modeling
- graph neural networks for relational data

### Hyperparameter Tuning
- Tune on validation data, never on the test set.
- Use search strategies appropriate to cost: grid, random, Bayesian optimization, bandit methods, or population-based training.
- Repeated peeking at validation performance can indirectly overfit the validation set.
- Reserve a final vaulted test set for last-step reporting.

### Regularization
To reduce overfitting:
- early stopping
- weight decay
- dropout
- data augmentation
- label smoothing
- ensembling
- sparsity penalties
- architecture constraints

### Calibration
A good classifier should output useful probabilities, not only correct labels.
- Check reliability diagrams and expected calibration error.
- Use Platt scaling, isotonic regression, or temperature scaling when needed.

---

## Deep Learning and Modern AI

### Neural Network Fundamentals
A neural network composes linear transformations with nonlinear activations.
- Common activations include ReLU, GELU, sigmoid, and tanh.
- Backpropagation computes gradients for all trainable parameters.
- Training depends heavily on initialization, optimizer choice, normalization, and batch size.

### Vanishing and Exploding Gradients
Deep networks can become hard to train when gradients shrink or blow up.
Mitigations include:
- careful initialization
- residual connections
- normalization layers
- gated recurrent units and LSTMs
- gradient clipping

### Convolutional Neural Networks
CNNs exploit spatial locality.
- local receptive fields capture nearby structure
- weight sharing reduces parameter count
- pooling and striding aggregate local information
- inductive bias makes CNNs effective in vision and some signal tasks

### Sequence Models and Attention
RNNs process inputs step-by-step, which can create bottlenecks for long sequences.
Attention addresses this by allowing direct interactions between tokens.
- A **Query** expresses what a token is seeking.
- A **Key** expresses what another token offers.
- A **Value** contains the information to aggregate.
- Attention weights determine how much each value contributes.

### Transformers
Transformers replace recurrence with stacked self-attention and feed-forward blocks.
Key components:
- token embeddings
- positional information
- multi-head self-attention
- residual connections
- normalization
- feed-forward sublayers

Strengths:
- parallel computation over sequences
- long-range dependency modeling
- flexible transfer learning across tasks

### Foundation Models and Transfer Learning
Modern practice often starts from pre-trained models.
- In computer vision, pre-trained CNNs or vision transformers are fine-tuned on downstream tasks.
- In NLP, encoder or decoder Transformers are adapted via fine-tuning, prompting, adapters, or low-rank updates.
- Transfer learning is usually more data-efficient than training from scratch.

### Generative Models
Major paradigms include:
- **GANs**: generator versus discriminator in adversarial training
- **VAEs**: probabilistic latent-variable models trained through variational inference
- **Normalizing Flows**: exact-density models based on invertible transformations
- **Diffusion Models**: generate samples by reversing a gradual noise process
- **Autoregressive models**: predict the next token, pixel, or patch sequentially

### Large Language Models
LLMs are foundation models trained with self-supervision on large corpora.
Important considerations:
- pretraining objective
- tokenizer design
- context length
- alignment and instruction tuning
- retrieval augmentation
- evaluation beyond benchmark scores
- hallucination risk
- cost, latency, and privacy

---

## Word Embeddings and Semantic Representations

### Word2Vec
Word2Vec learns dense vector representations of words from context.
The central idea is the **distributional hypothesis**: words used in similar contexts tend to have similar meanings.

Two main training strategies:
- **CBOW (Continuous Bag of Words)**: predict the center word from surrounding context words.
- **Skip-Gram**: predict surrounding context words from the center word.

How it works in practice:
1. A sliding window defines context around each target word.
2. The model learns embeddings that make observed word-context pairs likely.
3. Approximation methods such as negative sampling or hierarchical softmax make training efficient.
4. After training, the embedding matrix is used as the representation space.

Why it matters:
- semantically similar words cluster together
- vector arithmetic can sometimes reflect linguistic regularities
- embeddings provide dense inputs for downstream models

Limitations:
- one static vector per word sense
- sensitive to corpus bias
- weaker than contextual embeddings for polysemy and long-range context

### Distributional vs Denotational Semantics
- **Denotational semantics** focuses on reference or truth-conditional meaning: what an expression denotes in the world or in a formal model.
- **Distributional semantics** represents meaning through patterns of use in language data.

Practical distinction:
- Denotational approaches are common in formal logic, programming languages, and symbolic semantics.
- Distributional approaches dominate modern NLP because they scale from data and support learning-based representations.
- They are complementary rather than mutually exclusive: symbolic structure can constrain or enrich distributional models.

### From Static to Contextual Embeddings
The field evolved from:
- count-based vectors
- Word2Vec, GloVe, FastText
- contextual embeddings from ELMo, BERT, GPT-style models

Contextual embeddings assign different representations to the same word depending on usage, which helps with ambiguity and richer downstream reasoning.

---

## Evaluation Standards

### General Evaluation Rules
- Use evaluation metrics aligned with the real deployment objective.
- Separate model selection from final model assessment.
- Report uncertainty in results using confidence intervals, repeated runs, or statistical tests where appropriate.
- Compare against baselines and prior systems.
- Record both aggregate and subgroup performance.

### Classification Metrics
Use more than one metric.
- confusion matrix
- accuracy
- precision
- recall
- specificity
- F1-score
- ROC-AUC
- PR-AUC
- log loss
- calibration metrics

Notes:
- PR-AUC is often more informative than ROC-AUC for severe class imbalance.
- Threshold selection should reflect the cost of false positives and false negatives.

### Regression Metrics
- MAE
- MSE
- RMSE
- MAPE when appropriate and numerically stable
- R-squared
- quantile loss for interval estimation

### Ranking and Retrieval Metrics
For search, recommendation, or retrieval:
- precision at k
- recall at k
- MAP
- NDCG
- MRR

### Generative and Language Metrics
For text generation or language modeling:
- perplexity
- BLEU, ROUGE, METEOR, BERTScore where appropriate
- human evaluation for factuality, fluency, safety, and usefulness

### Online Evaluation
Offline gains do not guarantee real-world value.
- A/B testing and interleaving methods can validate practical impact.
- Guardrail metrics should include latency, failure rate, fairness, and business-side harms.

---

## Common Risks and Failure Modes

### Data Leakage
Typical sources:
- fitting scalers or imputers on the full dataset
- using future information in time-series tasks
- performing feature selection before splitting
- target leakage through proxy variables
- grouped-entity leakage across partitions

### Distribution Shift
Training and deployment distributions may diverge.
Important types:
- covariate shift
- label shift
- concept drift
- population drift
- instrumentation changes

### Label Problems
- noisy labels
- inconsistent instructions
- delayed or censored outcomes
- feedback loops where model outputs influence future labels

### Class Imbalance
Accuracy may hide poor minority-class performance.
Mitigations include:
- class-weighted losses
- focal loss
- resampling
- balanced mini-batches
- threshold tuning
- subgroup reporting

### Overfitting
Can occur to training data, to validation procedures, or even to public benchmarks.
Mitigation requires stronger evaluation discipline, not only more regularization.

### Shortcut Learning
Models may rely on spurious correlations instead of intended signal.
Examples include background artifacts in images, site-specific metadata in medical data, or annotation artifacts in NLP.

### Poor Uncertainty Handling
An apparently strong model may be dangerously overconfident under shift or on rare cases.

### Interpretability Gaps
Black-box models can create operational and legal problems in high-stakes settings if explanations are absent or misleading.

### Security and Privacy Risks
- membership inference
- model inversion
- prompt injection in LLM systems
- training data leakage
- unsafe tool use
- adversarial examples

---

## Best Practices for Leakage Prevention
- Split train, validation, and test before modeling decisions.
- Use pipelines so preprocessing is learned only on training data.
- Apply grouped or time-aware splits when needed.
- Prevent duplicate or near-duplicate records across partitions.
- Ensure feature definitions only use information available at prediction time.
- Keep a final vaulted test set untouched until the end.
- Review features for hidden target proxies.
- Reproduce evaluation from scratch with an independent script.
- Audit notebook workflows, because leakage often enters through ad hoc analysis.

---

## MLOps and Production Standards

### Experiment Tracking
Every run should log:
- code version
- data version
- hyperparameters
- random seeds
- training curves
- validation metrics
- hardware and runtime metadata
- artifacts such as checkpoints and plots

For deep learning, also log:
- gradient norms
- weight distributions
- activation statistics
- learning rate schedules

### Pipelines and Orchestration
Use pipelines to keep preprocessing and modeling atomic.
- scikit-learn Pipelines for classical workflows
- framework-specific dataset and transform classes for DL
- orchestration tools for scheduled training and scoring

### Model Registry and Deployment
- register models with lineage and approval status
- version inference interfaces
- verify feature parity between training and serving
- support rollback and shadow deployment when possible

### Monitoring
Monitor both technical and business performance.
- data drift
- concept drift
- calibration drift
- latency
- throughput
- failure rate
- fairness drift
- outcome degradation

### Testing
Required tests may include:
- unit tests for feature logic
- data validation tests
- reproducibility tests
- offline regression tests for model quality
- smoke tests for inference pipelines
- canary or shadow tests in production

---

## Responsible AI, Fairness, and Governance

### Fairness
Bias assessment should be routine, not exceptional.
- inspect representation bias in data collection
- compare error rates across relevant groups
- assess allocation harms and quality-of-service harms
- document unresolved trade-offs

### Explainability
Use the right kind of explanation for the use case.
- **White-box interpretability**: linear models, small trees, rule lists
- **Post hoc methods**: SHAP, LIME, counterfactual explanations, partial dependence, saliency maps

Explanations should support debugging and accountability, not merely presentation.

### Privacy and Compliance
- minimize personally identifiable information
- apply access controls and retention policies
- consider privacy-preserving methods when appropriate
- support auditability for regulated settings

### Documentation
Maintain:
- model cards
- data cards or datasheets
- evaluation reports
- incident logs
- deployment change logs

---

## Prior Art and Strong Defaults

### Reliable Tabular Defaults
For structured business data, strong starting points are often:
- logistic or linear regression
- random forests
- gradient boosted trees
- calibrated probability models

### Explainability Defaults
- coefficient analysis for linear models
- tree inspection for simple tree models
- SHAP for complex tabular models
- example-based error analysis for unstructured models

### Evaluation Defaults
- stratified cross-validation for small classification datasets
- grouped cross-validation when entities repeat
- time-based backtesting for forecasting
- confusion matrix and PR curves for imbalance-sensitive classification
- RMSE and MAE for regression

### Representation Defaults
- one-hot encoding for low-cardinality categories
- learned embeddings for high-cardinality categories or text
- transfer learning before training massive DL models from scratch

---

## Practical Decision Heuristics
- Prefer the simplest model that meets performance, robustness, and governance requirements.
- For tabular data, test tree ensembles before deep neural networks.
- For text, begin with TF-IDF plus a linear baseline, then evaluate Transformer-based transfer learning.
- For images, start from pre-trained CNN or vision transformer backbones.
- When data is limited, prioritize feature quality, transfer learning, uncertainty estimation, and conservative evaluation.
- When stakes are high, optimize for reliability, calibration, interpretability, and monitoring, not only raw benchmark scores.

---

## Compact Glossary
- **Bias-Variance Trade-off**: tension between underfitting and overfitting.
- **Calibration**: alignment between predicted probabilities and observed frequencies.
- **Concept Drift**: change in the relationship between inputs and target over time.
- **Cross-Validation**: repeated resampling procedure for more reliable performance estimation.
- **Double Descent**: modern non-monotonic test error pattern in overparameterized models.
- **Empirical Risk Minimization**: minimizing average loss on observed training data.
- **Feature Store**: system for consistent feature computation and reuse.
- **Foundation Model**: large pre-trained model adapted to many downstream tasks.
- **Inductive Bias**: assumptions built into a model class.
- **Leakage**: use of information during training that would not be available at prediction time.
- **Model Card**: structured documentation for model purpose, limitations, and evaluation.
- **Word2Vec**: shallow neural embedding method based on local context prediction.

---

## Recommended Minimal Standard for Any Serious Data Science Project
1. Define the target, business objective, and error costs.
2. Freeze the train/validation/test strategy before model building.
3. Create a simple, interpretable baseline.
4. Build preprocessing and modeling as a single pipeline.
5. Track every experiment with versions, seeds, and metrics.
6. Evaluate with task-appropriate metrics, calibration, and subgroup analysis.
7. Protect the test set from all tuning decisions.
8. Document assumptions, limitations, and deployment requirements.
9. Monitor drift, failures, and real-world impact after deployment.
10. Reassess fairness, privacy, and governance as the system evolves.

---

## Summary
A mature data science practice combines theory, experimentation, engineering rigor, and governance. Strong systems are not defined only by predictive accuracy, but by reliable data handling, leakage-free evaluation, reproducible workflows, appropriate model choice, principled uncertainty handling, operational monitoring, and transparent documentation. Modern practice spans classical ML, deep learning, foundation models, and generative AI, but the underlying standard remains the same: build models that are scientifically defensible, operationally robust, and fit for their real-world purpose.
