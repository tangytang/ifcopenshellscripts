from datetime import datetime
import os
import click
import subprocess


def convert_file(input_file, output_dir, method):
    """
    Convert a file using Potree Converter with a progress bar.

    :param input_file: Path to the input .las/.laz file
    :param output_dir: Directory to save the converted files
    :param method: Conversion method (e.g., poisson)
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        click.echo(f"Created output directory at {output_dir}")

    # Construct the PotreeConverter command
    command = [
        "PotreeConverter",
        input_file,
        "-o",
        output_dir,
        "-m",
        method
    ]

    try:
        click.echo("Starting conversion...")
        subprocess.run(command)
        click.echo("Conversion completed successfully.")
    except subprocess.CalledProcessError as e:
        click.echo("PotreeConverter failed.")
        click.echo(e)
        raise
    except Exception as e:
        click.echo("An unexpected error occurred during conversion.")
        click.echo(e)
        raise


@click.command()
@click.option('--input_file_path', prompt='file path',
              help='The path to the .las/.laz file.')
@click.option('--method', default='poisson', show_default=True,
              help='Conversion method for Potree Converter.')
def cli(input_file_path, method):
    """
    Command-Line Tool to Download a .ply File from S3, ensure a corresponding .las File Exists, Convert it using Potree Converter, and optionally upload the result.
    """
    try:
        output_dir = 'outputs/' + datetime.now().strftime('%Y%m%d_%H%M%S')
        # Convert the downloaded file with progress bar
        convert_file(input_file_path, output_dir, method)

        click.echo("Process completed successfully.")

    except FileNotFoundError as e:
        click.echo(f"File not found: {e}")
        click.echo("Process failed due to missing .las file.")
        raise
    except Exception as e:
        click.echo(f"An error occurred: {e}")
        click.echo("Process failed. Please check the logs for more details.")
        raise


if __name__ == '__main__':
    cli()
