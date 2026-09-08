# Lumos Server

This folder contains the backend: the socket.io server, the belief-elicitation and
belief-consistency metrics, the intervention trigger logic, and the LLM generation
pipeline. It also serves the built frontend from `public/`.

It is built on Lumos, originally designed by Narechania et al. \[1\]. The
interaction-focus metrics are inherited from it \[2\].

## Requirements

- `Python 3.10` - [Link](https://www.python.org/)
- `pip` - [Link](https://pypi.org/project/pip/) - package installer for Python
- `venv` - [Link](https://docs.python.org/3/library/venv.html) - Serves files in virtual environment

## Setup

### Create and Activate Virtual Environment

#### Windows
1. `py -3.10 -m venv venv` - create a python3 virtual environment called _venv_ in the current directory
2. `venv\Scripts\activate.bat` - enters the virtual environment
   - **FROM THIS POINT ON: only use `python` command to invoke interpreter, avoid using global command `py`!!**

#### MacOS/Linux
1. `python3.10 -m venv venv` / `virtualenv --python=python3.10 venv` - create a python3 virtual environment called venv
2. `source venv/bin/activate` - enters the virtual environment
   - **FROM THIS POINT ON: only use `python` command to invoke interpreter, avoid using global command `python3.10`!!**

### Install Packages
3. `python -m pip install --upgrade pip setuptools wheel`
4. `python -m pip install -r requirements.txt` - installs required libraries local to this project environment
5. `python -m pip install numpy --no-use-pep517` (for M1 Mac. For others, maybe try without `--no-use-pep517`)
6. `python -m pip install pandas --no-use-pep517` (for M1 Mac. For others, maybe try without `--no-use-pep517`)

## Run

1. Execute `python server.py`
2. This server is served at `http://localhost:3000`. The production build of the frontend (built using `ng build`) is served from the `public` directory. Check the [../app/README.md](../app/README.md) for more information. Update this in the `app` repository at `app/src/app/models/config.ts` > `DeploymentConfig.SERVER_URL`


### References

\[1\] - Narechania, Arpit and Coscia, Adam and Wall, Emily and Endert, Alex.
"Lumos: Increasing Awareness of Analytic Behavior during Visual Data Analysis."
*IEEE Transactions on Visualization and Computer Graphics* 28, no. 1 (2022): 1009-1018.

```bibTeX
@article{narechania2022lumos,
  author={Narechania, Arpit and Coscia, Adam and Wall, Emily and Endert, Alex},
  journal={{IEEE Transactions on Visualization and Computer Graphics}}, 
  title={{Lumos: Increasing Awareness of Analytic Behavior during Visual Data Analysis}}, 
  year={2022},
  volume={28},
  number={1},
  pages={1009-1018},
  doi={10.1109/TVCG.2021.3114827}
}
```

\[2\] - Reference withheld for anonymous review.
