import requests
from   nordpool import elspot, elbas
import json
from   datetime import timezone
from   datetime import date, datetime, timedelta
import sched, time
import pytz
import tzlocal
import matplotlib.pyplot as plt
import numpy as np
import sched, time
import sensibo_client as SC
import sys
import pickle
sys.path.append('/Applications/casadiPython3')
from casadi import *
from casadi.tools import *

plt.close("all")

# Where are we?
Zone = 'Tr.heim'                               # Spot Market : Trondheim
local_timezone=pytz.timezone('Europe/Oslo')    # Time zone   : Oslo

# Which data?
#FileName = 'Power_Test_2021-02-19_20_0'
#FileName = 'Power_Test_2021-02-20_23_15'
FileName = 'Power_Test_2021-02-21_21_0'


# Initialize Nordpool spot prices
#prices_spot = elspot.Prices()

## Stuff
Col = ['r','g','m','c']

# Measurement sampling time
SamplingTime = {'Measurement' :   5,   # Sampling time for measurement
                        'HP'  :  60   # Sampling time for HP
              }
########################  Some functions  ########################

def FindIndex(Times,time):
    # Find the time_index of time in Times
    assert(time >= Times[0])
    assert(time <= Times[-1])
    
    time_index = -1
    for k in range(0,len(Times)-1):
 
        if (Times[k]-time)*(Times[k+1]-time) < 0:
            time_index = k
        if (Times[k]-time) == 0:
            time_index = k
        if (Times[k+1]-time) == 0:
            time_index = k+1
        
    if time_index < 0:
        print('Issue to be checked')
        print(time_index)
        print(time)
        sys.exit()
        
    return time_index

def Average(Times,Data,time_start,time_end):

    assert(time_start >= Times[0])
    
    index_start = FindIndex(Times,time_start)
    index_end   = FindIndex(Times,time_end)
    
    Average = 0
    for index in range(index_start,index_end):
        if Times[index] >= time_start and Times[index+1] <= time_end:
            dt  = Times[index+1] - Times[index]
        if Times[index] < time_start and Times[index+1] <= time_end:
            dt  = Times[index+1] - time_start
        if Times[index] >= time_start and Times[index+1] > time_end:
            dt  = time_end - Times[index]
        if Times[index] < time_start and Times[index+1] > time_end:
            dt  = time_end - time_start
        
        Average += Data[index]*dt # Cumulate energy in J
        
    return Average


## Load experimental data

print('Process data file : '+FileName)

f = open(FileName+'.pkl',"rb")
Data = pickle.load(f)
f.close()

AveragingTime = 5#Data['StepDuration']

Pumps = list(Data['HP'].keys())

# Attribute colors
ColHP = {}
indexcol = 0
for HP in Pumps:
    ColHP[HP] = Col[indexcol]
    indexcol += 1


######################## Find beginning and end of measurements ##################

time_start = Data['Power']['Times'][0]

# Find latest time having measurements for all:
time_end = Data['EnergyRT']['Times'][-1]
for pump in Pumps:
    if Data['HP'][pump]['Times'][-1] < time_end:
        time_end = Data['HP'][pump]['Times'][-1]

time_span = (time_end - time_start).total_seconds()/60.

################ Create time grids for all items ################

Times = { 'Power' : [] }
## Average power over AveragingTime
for time in Data['EnergyRT']['Times']:
    Times['Power'].append( (time - time_start).total_seconds()/60.  )
    
for pump in Pumps:
    Times[pump] = []
    for time in Data['HP'][pump]['Times']:
        Times[pump].append( (time - time_start).total_seconds()/60.  )


time_grid = [0]
while time_grid[-1] + AveragingTime <= time_span:
    time_grid.append(time_grid[-1] + AveragingTime)

################ Compute cumulated power in [J] ##############

Energy = [0]
for k in range(len(Data['EnergyRT']['Times'])-1):
    dt      = (Data['EnergyRT']['Times'][k+1] - Data['EnergyRT']['Times'][k]).total_seconds()
    Energy.append( Energy[-1] + Data['EnergyRT']['Power'][k]*dt )
    
################   Average power   ##############

PowerAverage = []
for time in time_grid:
    Energy_start = np.interp(time                            , Times['Power'], Energy    )
    Energy_end   = np.interp(time + AveragingTime, Times['Power'], Energy    )
    
    Delta_Energy = (Energy_end - Energy_start)                         # in J
    AvPower      = Delta_Energy/(60*AveragingTime)/1e3     # in kW
    PowerAverage.append( AvPower )


################   Interpolate data  ##############

Temperatures = { 'Meas': {},
                 'Ref' : {}}
DTemp        = {}
PumpOn       = {}
for pump in Pumps:
    Temperatures['Meas'][pump] = np.interp(   time_grid, Times[pump], Data['HP'][pump]['Measurements']['temperature']    )
    Temperatures[ 'Ref'][pump] = np.interp(   time_grid, Times[pump], Data['HP'][pump]['States']['targetTemperature']    )
    PumpOn[pump]               = np.interp(   time_grid, Times[pump], Data['HP'][pump]['States']['on']                   )
    DTemp[pump]                = Temperatures[ 'Ref'][pump] - Temperatures['Meas'][pump]
    
########################  Extract indices when single pump on  ####

IndicesExperiment = []
for k, time in enumerate(time_grid):
    TotalOn = 0
    for pump in Pumps:
        TotalOn += PumpOn[pump][k]
    if TotalOn == 1:
        IndicesExperiment.append(k)
        

########################  Extract individual pump powers  ########################

DataOn = {}
for pump in Pumps:
    DataOn[pump] = {'DTemp' : [],
                    'Power' : [],
                    'Time'  : []}
    for k in IndicesExperiment:
        if PumpOn[pump][k]:
            DataOn[pump][ 'Time'].append(  time_grid[k]    )
            DataOn[pump]['DTemp'].append(  DTemp[pump][k]  )
            DataOn[pump]['Power'].append(  PowerAverage[k] )

########################  Regression  #############################

# Power = a*DTemp + b

# Linear system: min_p 0.5*(A*p - b)^2
#
# A.'*A*p = A.'*b => p = inv(A.'*A)*A.'*b
#
# where
#
#  [ DTemp    1  ] * [a] = [ Power ]
#  [   |      |  ]   [b]   [       ]
#  [   |      |  ]         [       ]

# Gives solid parameters...
Param = {}
for pump in Pumps:
    N = len(DataOn[pump]['DTemp'])
    A = np.concatenate( (np.array(DataOn[pump]['DTemp']).reshape(N,1), np.array([1]*N).reshape(N,1)), axis=1 )
    b = np.array(DataOn[pump]['Power']).reshape(N,1)
    if N > 0:
        Param[pump] = np.matmul(np.matmul(np.linalg.inv(np.matmul(A.T,A)),A.T),b)
    else:
        Param[pump] = []

########################  SYSID version I #############################

# Power_ref  = a*DTemp + b
# Power[k+1] = Power[k] + c*( Power_ref - Power[k] )
#
# Becomes:
#
# Power[k+1] = (1-c)*Power[k] + a*c*DTemp + c*b
#
# Yields:
#
# Power[k+1] = a*Power[k] + b*DTemp[k] + c
#
# SYSID does:
#
# min_{a,b,c} sum_k ( a*Power[k] + b*DTemp[k] + c - Power[k+1] )^2
#
# In linear form:
#
# [ Power[k]    DTemp[k]      1 ] * [a] = [ Power[k+1] ]
# [   |            |          | ]   [b]   [     |      ]
# [   |            |          | ]   [c]   [     |      ]
#

# Gives weird parameters...
ParamDyn = {}
for pump in Pumps:
    N = len(DataOn[pump]['DTemp'])-1
    
    A = np.concatenate( (np.array(DataOn[pump]['Power'][0:N]).reshape(N,1), np.array(DataOn[pump]['DTemp'][0:N]).reshape(N,1), np.array([1]*N).reshape(N,1)), axis=1 )
    b = np.array(DataOn[pump]['Power'][1:N+1]).reshape(N,1)
    if N > 0:
        ParamDyn[pump] = np.matmul(np.matmul(np.linalg.inv(np.matmul(A.T,A)),A.T),b)
    else:
        ParamDyn[pump] = []
 
########################  SYSID version II #############################

# Power_ref  = a*DTemp + b
# Power[k+1] = Power[k] + c*( Power_ref - Power[k] )
#
# SYSID does:
#
# min_{c} sum_k (  c*( Power_ref - Power[k] ) - (Power[k+1]-Power[k]) )^2
#
# In linear form:
#
# [ Power_ref - Power[k]  ] * [c] = [ Power[k+1]-Power[k] ]
# [           |           ]         [           |         ]
# [           |           ]         [           |         ]
#

# Gives c > 1, i.e. dynamics are not visible at a 5' time scale.
ParamDynSeparate = {}
for pump in Pumps:
    N = len(DataOn[pump]['DTemp'])-1
    if N > 0:
        Power_ref = Param[pump][0]*np.array(DataOn[pump]['DTemp'][0:N]) +  np.array([1]*N)*Param[pump][1]
        
        A = (Power_ref - np.array(DataOn[pump]['Power'][0:N])).reshape(N,1)
        b = (np.array(DataOn[pump]['Power'][1:N+1]) - np.array(DataOn[pump]['Power'][0:N])).reshape(N,1)

        ParamDynSeparate[pump] = np.matmul(np.matmul(np.linalg.inv(np.matmul(A.T,A)),A.T),b)
    else:
        ParamDynSeparate[pump] = []
        
########################  Constrained fitting ###########################

# Predict power using:
# Power_ref  = a*DTemp + b
# Power = log(1+exp(ReLu*(  Power_ref   ) )))/ReLu

ReLu = 30.

PumpParam = struct_symMX([  entry('Gain'),
                            entry('Const')  ])

PumpsParam = []
for pump in Pumps:
    PumpsParam += [entry( pump, struct = PumpParam)]
PumpsParam = struct_symMX( PumpsParam  )

J = 0
for pump in Pumps:
    N = len(DataOn[pump]['DTemp'])-1
    if N > 0:
        for k in range(N):
            Power_ref  = PumpsParam[pump][0]*DataOn[pump]['DTemp'][k] + PumpsParam[pump][1]
            PowerModel = log(1+exp(ReLu*(  Power_ref  ) ) )/ReLu
            J += ( PowerModel - DataOn[pump]['Power'][k] )**2

PumpsParam_Guess =  PumpsParam(0)

for pump in Pumps:
    if len(DataOn[pump]['DTemp']) > 0:
        PumpsParam_Guess[pump,'Gain']  = Param[pump][0]
        PumpsParam_Guess[pump,'Const'] = Param[pump][1]
    
lbw =  PumpsParam(-inf)
ubw =  PumpsParam(+inf)
print('############## Build NLP ##############')
prob = {'f': J, 'x': PumpsParam, 'g': []}
options = {}
options['ipopt'] = {}
solver = nlpsol('solver', 'ipopt', prob, options)
sol = solver(x0=PumpsParam_Guess, lbx=lbw, ubx=ubw, lbg=[], ubg=[])
param_opt = sol['x'].full().flatten()
param_opt  = PumpsParam(param_opt)
    
########################  Print parameters  #############################
print('#############  Static linear model  #############')
for pump in Pumps:
    if len(Param[pump]) > 0:
        print('HP     : '+pump)
        print('Gain   : '+str(Param[pump][0,0]))
        print('Const. : '+str(Param[pump][1,0]))
        print('----------------------------------------------')

"""
print('#############  Full dynamic model  #############')
for pump in Pumps:
    if len(Param[pump]) > 0:
        print('HP        : '+pump)
        print('Gain Pow  : '+str(ParamDyn[pump][0,0]))
        print('Gain Temp : '+str(ParamDyn[pump][1,0]))
        print('Const.    : '+str(ParamDyn[pump][2,0]))
        print('----------------------------------------------')

print('#############  Time-const. model  #############')
for pump in Pumps:
    if len(Param[pump]) > 0:
        print('HP     : '+pump)
        print('Gain   : '+str(ParamDynSeparate[pump][0,0]))
        print('----------------------------------------------')
"""
print('#############  Static nonlinear model  #############')
for pump in Pumps:
    print('HP     : '+pump)
    print('Gain   : '+str(param_opt[pump,'Gain']))
    print('Const. : '+str(param_opt[pump,'Const']))
    print('----------------------------------------------')

DataPickle = {}
for pump in Pumps:
    DataPickle[pump] = {}
    for item in ['Gain','Const']:
        DataPickle[pump][item] = float(param_opt[pump,item])


ParamName = FileName + '_Model_Fit'
f = open(ParamName+'.pkl',"wb")
pickle.dump(DataPickle,f, protocol=2)
f.close()

print('Averaging time : '+str(AveragingTime ))

###################### Do some stats on the model error ######################
plt.figure(4)
sp = 1
for pump in Pumps:
    if len(Param[pump]) > 0:
        PowModel = Param[pump][0]*np.array(DataOn[pump]['DTemp']) + Param[pump][1]
        Error    = PowModel - np.array(DataOn[pump]['Power'])
        plt.subplot(2,2,sp)
        plt.hist(Error,10)
        sp += 1
 
########################  Plot stuff  #############################
plt.figure(1)
sp = 1
for pump in Pumps:
    plt.subplot(2,2,sp)
    dT = np.array(Data['HP'][pump]['States']['targetTemperature']) - np.array(Data['HP'][pump]['Measurements']['temperature'])
    plt.step(Times['Power'],np.array(Data['EnergyRT']['Power'])/1e3,linestyle='-',color=[.6,.6,.6],where='post')
    plt.step(Times[pump],dT,linestyle=':',color=[.6,.6,.6],where='post')
    
    plt.step(time_grid,PumpOn[pump],linestyle='--',color=ColHP[pump],where='post')
    plt.step(time_grid,DTemp[pump] ,linestyle=':',color=ColHP[pump],where='post')
    plt.step(time_grid,PowerAverage,linestyle='-',color=ColHP[pump],where='post')
    plt.xlabel('Time')
    plt.ylabel('Power')
    plt.title(pump)
    plt.legend(['Raw pow.','Raw Dtemp','on','Dtemp','Pow.'])
    sp += 1

plt.figure(2)
sp = 1
for pump in Pumps:
    #Leg.append(pump)
    plt.subplot(2,2,sp)
    DtempDisp = np.linspace(-4,8,100)
    plt.plot(DataOn[pump]['DTemp'],DataOn[pump]['Power'],linestyle='none',marker='.',markersize=10,color=ColHP[pump])
    Power_ref  = param_opt[pump,'Gain']*DtempDisp + param_opt[pump,'Const']
    PowerModel = log(1+exp(ReLu*(  Power_ref  ) ) )/ReLu
    
    if len(Param[pump]) > 0:
        PowerReg  = Param[pump][0]*DtempDisp + Param[pump][1]
        plt.plot(DtempDisp,PowerReg,linestyle='-',color=ColHP[pump])
        plt.plot(DtempDisp,PowerModel,linestyle='--',color=ColHP[pump])
    plt.xlabel('Delta Temp.')
    plt.ylabel('Power')
    plt.title(pump)
    
    sp += 1
    
plt.figure(3)
sp = 1
for pump in Pumps:
    plt.subplot(2,2,sp)
    if len(DataOn[pump]['DTemp']) > 0:
        Power_ref  = param_opt[pump,'Gain']*DataOn[pump]['DTemp'] + param_opt[pump,'Const']
        PowerModel = log(1+exp(ReLu*(  Power_ref  ) ) )/ReLu
        PowerReg   = Param[pump][0]*DataOn[pump]['DTemp'] + Param[pump][1]
        plt.step(DataOn[pump][ 'Time'],PowerReg ,linestyle=':',color='r')
        plt.step(DataOn[pump][ 'Time'],PowerModel ,linestyle='--',color='r')

    plt.step(DataOn[pump][ 'Time'],DataOn[pump]['DTemp'],linestyle='-',color='k')
    plt.step(DataOn[pump][ 'Time'],DataOn[pump]['Power'],linestyle='-',color='b')
    
    plt.xlabel('Time')
    plt.ylabel('Power')
    plt.legend(['Power','Delta temp.'])
    plt.title(pump)
    sp += 1

plt.show(block=False)

