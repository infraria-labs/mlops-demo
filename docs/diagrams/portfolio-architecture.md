# Portfolio architecture

The lighter `meteo-mlops-light` version uses domain-generic labels and groups the core architecture into eight components, with one function per component. It keeps the model approval and application review boundaries. The README uses the detailed version, which includes the separate learning environments. Both versions are available as editable draw.io, self-contained SVG, and PNG files.

The generic training card uses Python and PyTorch logos as requested for the portfolio illustration. The demonstrator's implemented baseline uses scikit-learn and XGBoost; this visual change does not change its training dependencies.

The diagram presents the repository architecture at a component level, including the model lifecycle, application CI/CD, GitOps, serving, observability, infrastructure, and separate learning environments. It describes implemented capabilities, not the current running state of an AWS cluster.

Use `meteo-mlops-portfolio.drawio` for editing, `meteo-mlops-portfolio.drawio.svg` for scalable publication, and `meteo-mlops-portfolio.png` for presentations. The SVG also embeds editable draw.io XML. Images embed their logos and need no remote resources to display.

Model approval, numeric version selection, release PR review, and Argo CD sync are explicit operator actions. Scheduled training is enabled deliberately. EKS is a temporary validation environment; Kubeflow Pipelines is an isolated local experiment. They are not dependencies of the core AWS k3s deployment.

The high-level diagram groups implementation dependencies rather than showing every Python package or Kubernetes object. Data-source and operator cards show the technology used to perform the step. ECR and SSM/VPC use the AWS brand mark; EC2/EBS uses the EC2 mark; these are grouped cards, not distinct logos for every AWS service.

Architecture evidence: [MLflow](../mlflow.md), [model lifecycle](../retraining.md), [CI/CD](../cicd-helm.md), [GitOps](../gitops.md), [AWS k3s](../aws-k3s.md), [observability](../observability.md), [EKS](../eks.md), [Kubeflow lab](../kubeflow-local.md), and the repository workflows, Terraform, Helm charts, and locked Python dependencies.

Logo sources: [Simple Icons](https://github.com/simple-icons/simple-icons) (current icons and v11 AWS marks), [Kubeflow website](https://github.com/kubeflow/website/blob/master/static/images/logo.svg), and [XGBoost / DMLC artwork](https://github.com/dmlc/dmlc.github.io/blob/master/img/logo-m/xgboost.png). Logos remain the property of their respective owners.
