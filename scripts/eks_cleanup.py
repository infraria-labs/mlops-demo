"""Capture exact exercise resource IDs, then verify their removal after Terraform destroy.

Run with the same AWS_PROFILE as Terraform. Does not delete anything.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import boto3
from botocore.exceptions import ClientError

INVENTORY = Path('.eks/cleanup-inventory.json')


def absent(call, codes, **kwargs):
    try:
        call(**kwargs)
    except ClientError as error:
        if error.response['Error']['Code'] in codes:
            return True
        raise
    return False


def capture():
    outputs = json.loads(subprocess.check_output(
        ['terraform', '-chdir=infra/eks', 'output', '-json'], text=True))
    values = {key: item['value'] for key, item in outputs.items()}
    ec2 = boto3.client('ec2', region_name=values['region'])
    reservations = ec2.describe_instances(Filters=[
        {'Name': 'vpc-id', 'Values': [values['vpc_id']]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
    ])['Reservations']
    instances = [instance for reservation in reservations for instance in reservation['Instances']]
    root_volumes = [mapping['Ebs']['VolumeId'] for instance in instances
                    for mapping in instance.get('BlockDeviceMappings', []) if 'Ebs' in mapping]
    context = 'eks-' + values['cluster_name']
    pvs = json.loads(subprocess.check_output([
        'kubectl', '--kubeconfig', str(Path('.eks/kubeconfig').resolve()), '--context', context,
        'get', 'pv', '-o', 'json'], text=True))['items']
    csi_volumes = [pv['spec']['csi']['volumeHandle'] for pv in pvs
                   if pv['spec'].get('storageClassName') == 'eks-gp3']
    nodegroup = boto3.client('eks', region_name=values['region']).describe_nodegroup(
        clusterName=values['cluster_name'], nodegroupName='demo')['nodegroup']
    values['autoscaling_groups'] = [g['name'] for g in nodegroup['resources']['autoScalingGroups']]
    values['instance_ids'] = [instance['InstanceId'] for instance in instances]
    values['volume_ids'] = sorted(set(root_volumes + csi_volumes))
    values['captured_at_utc'] = datetime.now(timezone.utc).isoformat()
    INVENTORY.parent.mkdir(exist_ok=True)
    INVENTORY.write_text(json.dumps(values, indent=2) + '\n')
    INVENTORY.chmod(0o600)
    print(json.dumps({'inventory': str(INVENTORY), 'instances': len(instances),
                      'volumes': len(values['volume_ids'])}))


def verify():
    values = json.loads(INVENTORY.read_text())
    session = boto3.Session(region_name=values['region'])
    ec2, eks, iam, s3 = [session.client(service) for service in ('ec2', 'eks', 'iam', 's3')]
    checks = {}
    checks['cluster_removed'] = absent(eks.describe_cluster, {'ResourceNotFoundException'},
                                       name=values['cluster_name'])
    checks['vpc_removed'] = absent(ec2.describe_vpcs, {'InvalidVpcID.NotFound'},
                                   VpcIds=[values['vpc_id']])
    checks['bucket_removed'] = absent(s3.head_bucket, {'404', 'NoSuchBucket', 'NotFound'},
                                      Bucket=values['artifact_bucket'])
    for volume in values['volume_ids']:
        checks['removed_' + volume] = absent(ec2.describe_volumes, {'InvalidVolume.NotFound'},
                                             VolumeIds=[volume])
    for instance in values['instance_ids']:
        try:
            found = ec2.describe_instances(InstanceIds=[instance])['Reservations']
            checks['terminated_' + instance] = all(
                item['State']['Name'] == 'terminated' for group in found for item in group['Instances'])
        except ClientError as error:
            if error.response['Error']['Code'] != 'InvalidInstanceID.NotFound':
                raise
            checks['terminated_' + instance] = True
    for suffix in ('cluster', 'node', 'ebs', 'mlflow'):
        role = values['cluster_name'] + '-' + suffix
        checks['removed_role_' + suffix] = absent(iam.get_role, {'NoSuchEntity'}, RoleName=role)
    checks['no_autoscaling_groups'] = not session.client('autoscaling').describe_auto_scaling_groups(
        AutoScalingGroupNames=values['autoscaling_groups'])['AutoScalingGroups']
    checks['no_exercise_snapshots'] = not ec2.describe_snapshots(OwnerIds=['self'],
        Filters=[{'Name': 'volume-id', 'Values': values['volume_ids']}])['Snapshots']
    checks['no_network_interfaces'] = not ec2.describe_network_interfaces(
        Filters=[{'Name': 'vpc-id', 'Values': [values['vpc_id']]}])['NetworkInterfaces']
    checks['no_nat_gateways'] = not [g for g in ec2.describe_nat_gateways(
        Filter=[{'Name': 'vpc-id', 'Values': [values['vpc_id']]}])['NatGateways'] if g['State'] != 'deleted']
    checks['empty_terraform_state'] = not subprocess.check_output(
        ['terraform', '-chdir=infra/eks', 'state', 'list'], text=True).strip()
    result = {'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'checks': checks}
    Path('.eks/teardown-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not all(checks.values()):
        raise RuntimeError('Exercise resources remain; inspect failed checks and retain Terraform state')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['capture', 'verify'])
    action = parser.parse_args().action
    capture() if action == 'capture' else verify()
