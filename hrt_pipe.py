from re import L
import numpy as np 
import os.path
from astropy.io import fits
import random, statistics
import subprocess
from scipy.ndimage import gaussian_filter
import matplotlib.pyplot as plt
import time

from utils import *


def get_data(path):
    """load science data from path"""
    try:
        data, header = load_fits(path)
      
        data /=  256. #conversion from 24.8bit to 32bit

        accu = header['ACCACCUM']*header['ACCROWIT']*header['ACCCOLIT'] #getting the number of accu from header

        data /= accu

        printc(f"Dividing by number of accumulations: {accu}",color=bcolors.OKGREEN)
        
        return data, header

    except Exception:
        printc("ERROR, Unable to open fits file: {}",path,color=bcolors.FAIL)

   

def demod_hrt(data,pmp_temp):
    '''
    Use constant demodulation matrices to demodulate data
    '''
 
    if pmp_temp == '50':
        demod_data = np.array([[ 0.28037298,  0.18741922,  0.25307596,  0.28119895],
                     [ 0.40408596,  0.10412157, -0.7225681,   0.20825675],
                     [-0.19126636, -0.5348939,   0.08181918,  0.64422774],
                     [-0.56897295,  0.58620095, -0.2579202,   0.2414017 ]])
        
    elif pmp_temp == '40':
        demod_data = np.array([[ 0.26450154,  0.2839626,   0.12642948,  0.3216773 ],
                     [ 0.59873885,  0.11278069, -0.74991184,  0.03091451],
                     [ 0.10833212, -0.5317737,  -0.1677862,   0.5923593 ],
                     [-0.46916953,  0.47738808, -0.43824592,  0.42579797]])
    
    else:
        printc("Demodulation Matrix for PMP TEMP of {pmp_temp} deg is not available", color = bcolors.FAIL)

    printc(f'Using a constant demodulation matrix for a PMP TEMP of {pmp_temp} deg',color = bcolors.OKGREEN)
    
    demod_data = demod_data.reshape((4,4))
    shape = data.shape
    demod = np.tile(demod_data, (shape[0],shape[1],1,1))

    if data.ndim == 5:
        #if data array has more than one scan
        data = np.moveaxis(data,-1,0) #moving number of scans to first dimension

        stokes_arr = np.matmul(demod,data)
        stokes_arr = np.moveaxis(stokes_arr,0,-1) #move scans back to the end
    
    elif data.ndim == 4:
        #for if data has just one scan
        stokes_arr = np.matmul(demod,data)
    
    return stokes_arr, demod
    


def phihrt_pipe(data_f,dark_f,flat_f,norm_f = True, clean_f = False, sigma = 59, flat_states = 24, 
                pmp_temp = '50',flat_c = True,dark_c = True, demod = True, norm_stokes = True, 
                out_dir = './',  out_demod_file = False,  correct_ghost = False, 
                ItoQUV = False, rte = False):

    '''
    PHI-HRT data reduction pipeline
    1. read in science data (+scaling) open path option + open for several scans at once
    2. read in flat field - just one, so any averaging must be done before
    3. option to clean flat field with unsharp masking
    4. read in dark field
    5. apply dark field
    6. normalise flat field
    7. apply flat field
    8. prefilter correction - not implemented yet
    9. read in field stop
    10. apply field stop
    11. demodulate with const demod matrix
    12. normalise to quiet sun
    13. calibration
        a) ghost correction - not implemented yet
        b) cross talk correction - not implemented yet
    14. rte inversion with cmilos

    Parameters
    ----------
        Input:
    data_f : list or string
        list containing paths to fits files of the raw HRT data OR string of path to one file  
    dark_f : string
        Fits file of a dark file (ONLY ONE FILE)
    flat_f : string
        Fits file of a HRT flatfield (ONLY ONE FILE)

    ** Options:
    norm_f: bool, DEFAULT: True
        to normalise the flat fields before applying
    clean_f: bool, DEFAULT: False
        clean the flat field with unsharp masking
    sigma: int, DEFAULT: 59
        sigma of the gaussian convolution used for unsharp masking if clean_f == True      
    flat_states: int, DEFAULT: 24
        Number of flat fields to be applied, options are 4 (one for each pol state), 6 (one for each wavelength), 24 (one for each image)
    pmp_temp: str, DEFAULT: '50'
        temperature of the PMP, to select the correct demodulation matrix
    flat_c: bool, DEFAULT: True
        apply flat field correction
    dark_c: bool, DEFAULT: True
        apply dark field correction
    demod: bool, DEFAULT: True
        apply demodulate to the stokes
    norm_stokes: bool, DEFAULT: True
        normalise the stokes vector to the quiet sun (I_continuum)
    out_dir : string, DEFUALT: './'
        directory for the output files
    out_demod_file: bool, DEFAULT: False
        output file with the stokes vectors to fits file
    correct_ghost: bool, DEFAULT: False 
        correct the ghost in bottom left corner
    ItoQUV: bool, DEFAULT: False 
        apply I -> Q,U,V correction
    rte: bool, DEFAULT: False 
        invert using cmilos
    out_rte_file: bool, DEFAULT: False
        output of rte result to fits file
    
    Returns
    -------
    data: nump array
        stokes vector 

    References
    ----------
    SPGYlib

    '''

    printc('--------------------------------------------------------------',bcolors.OKGREEN)
    printc('PHI HRT data reduction software  ',bcolors.OKGREEN)
    printc('--------------------------------------------------------------',bcolors.OKGREEN)

    overall_time = time.time()
    #-----------------
    # READ DATA
    #-----------------
    
    print(" ")
    printc('-->>>>>>> Reading Data              ',color=bcolors.OKGREEN) 

    start_time = time.time()

    if isinstance(data_f, list):
        #if the data_f contains several scans
        printc(f'Input contains {len(data_f)} scan(s)',color=bcolors.OKGREEN)
        
        number_of_scans = len(data_f)

        data_arr = [0]*number_of_scans
        hdr_arr = [0]*number_of_scans

        wve_axis_arr = [0]*number_of_scans
        cpos_arr = [0]*number_of_scans

        for scan in range(number_of_scans):
            data_arr[scan], hdr_arr[scan] = get_data(data_f[scan])

            if hdr_arr[scan]['BITPIX'] == 16:

                print(f"This scan: {data_f[scan]} has a bits per pixel of: 16 \nPerforming the extra scaling")

                data_arr[scan] *= 81920/127 #conversion factor if 16 bits

            wve_axis_arr[scan], _, _, cpos_arr[scan] = fits_get_sampling(data_f[scan],verbose = True)


        #--------
        #test if the scans have different sizes
        #--------

        first_shape = data_arr[scan].shape

        result = all(element.shape == first_shape for element in data_arr)
        if (result):
            print("All the scans have the same dimension")

        else:
            print("The scans have different dimensions! \n Ending process")

            exit()


        #--------
        #test if the scans have different continuum wavelength_positions
        #--------

        first_cpos = cpos_arr[0]
        result = all(c_position == first_cpos for c_position in cpos_arr)
        if (result):
            print("All the scans have the same continuum wavelength position, shifting all to 0th index if not already there")

        else:
            print("The scans have different continuum_wavelength postitions! \n Attempting to shift all to the 0th index")

        data = np.stack(data_arr, axis = -1)
        data = np.moveaxis(data, 0,-2) #so that it is [y,x,24,scans]

        print(f"Data shape is {data.shape}")

    elif isinstance(data_f, str):
        #case when data f is just one file
        data, header = get_data(data_f)
        data = np.expand_dims(data, axis = -1) #so that it has the same dimensions as several scans
        data = np.moveaxis(data, 0, -2) #so that it is [y,x,24,1]

        if header['BITPIX'] == 16:
    
            print(f"This scan: {data_f} has a bits per pixel is: 16 \n Performing the extra scaling")

            data *= 81920/127 #conversion factor if 16 bits

  
        print(f"Data shape is {data.shape}")

        wave_axis, _, _, cpos = fits_get_sampling(data_f,verbose = True)

        print("The data continuum wavelength position is at index: ", cpos)

        cpos_arr = list(cpos)

        if cpos_arr[0] != 0 and cpos_arr[0] != 5:
            print("Data continuum position not at 0 or 5th index. Please reconcile. \n Ending Process")

            exit()

    else:
      printc("ERROR, data_f argument is neither a string nor list containing strings: {} \n Ending Process",data_f,color=bcolors.FAIL)
      exit()
      
    data_shape = data.shape

    data_size = data_shape[:2]
    
    #converting to [y,x,pol,wv,scans]

    data = data.reshape(data_size[0],data_size[1],6,4,data_shape[-1]) #separate 24 images, into 6 wavelengths, with each 4 pol states
    data = np.moveaxis(data, 2,-2) #need to swap back to work

    #enabling cropped datasets, so that the correct regions of the dark field and flat field are applied
    print("Data reshaped to: ", data.shape)

    diff = 2048-data_size[0] #handling 0/2 errors
    
    if np.abs(diff) > 0:
    
        start_row = int((2048-data_size[0])/2)
        start_col = int((2048-data_size[1])/2)
        
    else:
        start_row, start_col = 0, 0
        
    printc('--------------------------------------------------------------',bcolors.OKGREEN)
    printc(f"------------ Load science data time: {np.round(time.time() - start_time,3)} seconds",bcolors.OKGREEN)
    printc('--------------------------------------------------------------',bcolors.OKGREEN)

    #-----------------
    # TODO: Could check data dimensions? As an extra fail safe before progressing?
    #-----------------
    
    
    #-----------------
    # READ FLAT FIELDS
    #-----------------

    if flat_c:
        print(" ")
        printc('-->>>>>>> Reading Flats',color=bcolors.OKGREEN)

        start_time = time.time()
    
        try:
            flat, header_flat = get_data(flat_f)

            print(f"Flat field shape is {flat.shape}")
            
            if header_flat['BITPIX'] == 16:
    
                print("Number of bits per pixel is: 16")

                flat *= 614400/128

            flat = np.moveaxis(flat, 0,-1) #so that it is [y,x,24]
            flat = flat.reshape(data_size[0],data_size[1],6,4) #separate 24 images, into 6 wavelengths, with each 4 pol states
            flat = np.moveaxis(flat, 2,-1)
            
            print(flat.shape)

            _, _, _, cpos_f = fits_get_sampling(flat_f,verbose = True)

            print(f"The continuum position of the flat field is at {cpos_f} index position")
            
            if flat_f[-62:] == 'solo_L0_phi-hrt-flat_0667134081_V202103221851C_0162201100.fits':
                
                print("This flat needs to be rolled")
                
                flat = np.roll(flat, 1, axis = -1)

                cpos_f = cpos_arr[0]

            #if continuum wavelength of the flat is not the same as the data, attempt to roll
            if cpos_f != cpos_arr[0]:
                print("The flat field continuum position is not the same as the data, please check your input data. \n Ending Process")

                exit()
                
            printc('--------------------------------------------------------------',bcolors.OKGREEN)
            printc(f"------------ Load flats time: {np.round(time.time() - start_time,3)} seconds",bcolors.OKGREEN)
            printc('--------------------------------------------------------------',bcolors.OKGREEN)

        except Exception:
            printc("ERROR, Unable to open flats file: {}",flat_f,color=bcolors.FAIL)


    else:
        print(" ")
        printc('-->>>>>>> No flats mode',color=bcolors.WARNING)


    #-----------------
    # READ AND CORRECT DARK FIELD
    #-----------------

    if dark_c:
        print(" ")
        printc('-->>>>>>> Reading Darks                   ',color=bcolors.OKGREEN)

        start_time = time.time()

        try:
            dark,h = get_data(dark_f)

            dark_shape = dark.shape

            if dark_shape != (2048,2048):
                
                printc("Dark Field Input File not in 2048,2048 format: {}",dark_f,color=bcolors.WARNING)
                printc("Attempting to correct ",color=bcolors.WARNING)

                
                try:
                    if dark_shape[0] > 2048:
                        dark = dark[dark_shape[0]-2048:,:]
                
                except Exception:
                    printc("ERROR, Unable to correct shape of dark field data: {}",dark_f,color=bcolors.FAIL)

            printc('--------------------------------------------------------------',bcolors.OKGREEN)
            printc(f"------------ Load darks time: {np.round(time.time() - start_time,3)} seconds",bcolors.OKGREEN)
            printc('--------------------------------------------------------------',bcolors.OKGREEN)

        except Exception:
            printc("ERROR, Unable to open darks file: {}",dark_f,color=bcolors.FAIL)


        #-----------------
        # APPLY DARK CORRECTION 
        #-----------------    
        print(" ")
        print("'-->>>>>>> Subtracting dark field")
        
        start_time = time.time()

        flat -= dark[..., np.newaxis, np.newaxis]
        
        data -= dark[start_row:start_row + data_size[0],start_col:start_col + data_size[1], np.newaxis, np.newaxis, np.newaxis] 

        printc('--------------------------------------------------------------',bcolors.OKGREEN)
        printc(f"------------- Dark Field correction time: {np.round(time.time() - start_time,3)} seconds",bcolors.OKGREEN)
        printc('--------------------------------------------------------------',bcolors.OKGREEN)


    #-----------------
    # OPTIONAL Unsharp Masking clean the flat field stokes V images
    #-----------------

    if clean_f:
        print(" ")
        printc('-->>>>>>> Cleaning flats with Unsharp Masking (Stokes V only)',color=bcolors.OKGREEN)

        start_time = time.time()

        #cleaning the stripe in the flats for a particular flat

        if flat_f[-62:] == 'solo_L0_phi-hrt-flat_0667134081_V202103221851C_0162201100.fits': 
            flat[1345, 296:, 1, 2] = flat[1344, 296:, 1, 2]
            flat[1346, :291, 1, 2] = flat[1345, :291, 1, 2]

        #demod the flats

        flat_demod, demodM = demod_hrt(flat, pmp_temp)

        norm_factor = norm_factor = np.mean(flat_demod[512:1536,512:1536,0,0])

        flat_demod /= norm_factor

        new_demod_flats = np.copy(flat_demod)

        b_arr = np.zeros((2048,2048,3,5))

        if cpos_arr[0] == 0:
            wv_range = range(1,6)

        elif cpos_arr[0] == 5:
            wv_range = range(5)

        for pol in range(3,4):

            for wv in wv_range: #not the continuum

                a = np.copy(np.clip(flat_demod[:,:,pol,wv], -0.02, 0.02))
                b = a - gaussian_filter(a,sigma)
                b_arr[:,:,pol-1,wv-1] = b
                c = a - b

                new_demod_flats[:,:,pol,wv] = c

        invM = np.linalg.inv(demodM)

        flat = np.matmul(invM, new_demod_flats*norm_factor)


        printc('--------------------------------------------------------------',bcolors.OKGREEN)
        printc(f"------------- Cleaning flat time: {np.round(time.time() - start_time,3)} seconds",bcolors.OKGREEN)
        printc('--------------------------------------------------------------',bcolors.OKGREEN)


    #-----------------
    # NORM FLAT FIELDS
    #-----------------

    if norm_f and flat_c:
        
        print(" ")
        printc('-->>>>>>> Normalising Flats',color=bcolors.OKGREEN)

        start_time = time.time()

        try:
            norm_fac = np.mean(flat[512:1536,512:1536, :, :], axis = (0,1))  #mean of the central 1k x 1k
            flat /= norm_fac[np.newaxis, np.newaxis, ...]

            printc('--------------------------------------------------------------',bcolors.OKGREEN)
            printc(f"------------- Normalising flat time: {np.round(time.time() - start_time,3)} seconds",bcolors.OKGREEN)
            printc('--------------------------------------------------------------',bcolors.OKGREEN)

        except Exception:
            printc("ERROR, Unable to normalise the flat fields: {}",flat_f,color=bcolors.FAIL)


    #-----------------
    # APPLY FLAT CORRECTION 
    #-----------------

    if flat_c:
        print(" ")
        printc('-->>>>>>> Correcting Flatfield',color=bcolors.OKGREEN)

        start_time = time.time()

        try:

            if flat_states == 6:
        
                printc("Dividing by 6 flats, one for each wavelength",color=bcolors.OKGREEN)
                    
                tmp = np.mean(flat,axis=-2) #avg over pol states for the wavelength

                data /= tmp[start_row:start_row + data_size[0],start_col:start_col + data_size[1], np.newaxis, np.newaxis]


            elif flat_states == 24:

                printc("Dividing by 24 flats, one for each image",color=bcolors.OKGREEN)

                data /= flat[start_row:start_row + data_size[0],start_col:start_col + data_size[1], :, :, np.newaxis] #only one new axis for the scans
                    
            elif flat_states == 4:

                printc("Dividing by 4 flats, one for each pol state",color=bcolors.OKGREEN)

                tmp = np.mean(flat,axis=-1) #avg over wavelength

                data /= tmp[start_row:start_row + data_size[0],start_col:start_col + data_size[1], np.newaxis, np.newaxis]
        
            printc('--------------------------------------------------------------',bcolors.OKGREEN)
            printc(f"------------- Flat Field correction time: {np.round(time.time() - start_time,3)} seconds ",bcolors.OKGREEN)
            printc('--------------------------------------------------------------',bcolors.OKGREEN)

        except: 
          printc("ERROR, Unable to apply flat fields",color=bcolors.FAIL)


    #-----------------
    # FIELD STOP 
    #-----------------
    
    print(" ")
    printc("-->>>>>>>  Applying field stop",color=bcolors.OKGREEN)

    start_time = time.time()
    
    field_stop,_ = load_fits('./field_stop/HRT_field_stop.fits')

    field_stop = np.where(field_stop > 0,1,0)

    data *= field_stop[start_row:start_row + data_size[0],start_col:start_col + data_size[1],np.newaxis, np.newaxis, np.newaxis]

    printc('--------------------------------------------------------------',bcolors.OKGREEN)
    printc(f"------------- Field stop time: {np.round(time.time() - start_time,3)} seconds",bcolors.OKGREEN)
    printc('--------------------------------------------------------------',bcolors.OKGREEN)


    #-----------------
    # GHOST CORRECTION  
    #-----------------

    # if correct_ghost:
    #     printc('-->>>>>>> Correcting ghost image ',color=bcolors.OKGREEN)

  
    #-----------------
    # APPLY DEMODULATION 
    #-----------------

    if demod:

        print(" ")
        printc('-->>>>>>> Demodulating data         ',color=bcolors.OKGREEN)

        start_time = time.time()

        data,_ = demod_hrt(data, pmp_temp)
        
        printc('--------------------------------------------------------------',bcolors.OKGREEN)
        printc(f"------------- Demodulation time: {np.round(time.time() - start_time,3)} seconds ",bcolors.OKGREEN)
        printc('--------------------------------------------------------------',bcolors.OKGREEN)


    #-----------------
    # APPLY NORMALIZATION 
    #-----------------

    if norm_stokes:
        
        print(" ")
        printc('-->>>>>>> Normalising Stokes to Quiet Sun',color=bcolors.OKGREEN)
        
        start_time = time.time()

        for scan in range(data_shape[-1]):
            
            I_c = np.mean(data[512:1536,512:1536,0,0,int(scan)]) #mean of central 1k x 1k of continuum stokes I - all data cpos shifted to 0
            data[:,:,:,:,scan] = data[:,:,:,:,scan]/I_c
       
        printc('--------------------------------------------------------------',bcolors.OKGREEN)
        printc(f"------------- Stokes Normalising time: {np.round(time.time() - start_time,3)} seconds ",bcolors.OKGREEN)
        printc('--------------------------------------------------------------',bcolors.OKGREEN)


    #-----------------
    # CROSS-TALK CALCULATION 
    #-----------------

    if ItoQUV:
        
        print(" ")
        printc('-->>>>>>> Cross-talk correction from Stokes I to Stokes Q,U,V ',color=bcolors.OKGREEN)
   

    #-----------------
    #CHECK FOR INFs
    #-----------------

    data[np.isinf(data)] = 0
    data[np.isnan(data)] = 0

    
    if out_demod_file:
        
        if isinstance(data_f, list):
            print(" ")
            printc('Saving demodulated data to one _reduced.fits file per scan')

            for count, scan in enumerate(data_f):

                with fits.open(scan) as hdu_list:

                    hdu_list[0].data = data[:,:,:,:,count]
                    hdu_list.writeto(out_dir + str(scan.split('.fits')[0][-10:]) + '_reduced.fits', overwrite=True)

        if isinstance(data_f, str):
            print(" ")
            printc('Saving demodulated data to a _reduced.fits file')

            with fits.open(data_f) as hdu_list:

                hdu_list[0].data = data
                hdu_list.writeto(out_dir + str(data_f.split('.fits')[0][-10:]) + '_reduced.fits', overwrite=True)


    #-----------------
    # INVERSION OF DATA WITH CMILOS
    #-----------------

    if rte == 'RTE' or rte == 'CE' or rte == 'CE+RTE':

        print(" ")
        printc('---------------------RUNNING CMILOS --------------------------',color=bcolors.OKGREEN)

        start_time = time.time()
        try:
            CMILOS_LOC = os.path.realpath(__file__)

            CMILOS_LOC = CMILOS_LOC[:-11] + 'cmilos/' #11 as hrt_pipe.py is 11 characters

            if os.path.isfile(CMILOS_LOC+'milos'):
                printc("Cmilos executable located at:", CMILOS_LOC,color=bcolors.WARNING)

            else:
                raise ValueError('Cannot find cmilos:', CMILOS_LOC)

        except ValueError as err:
            printc(err.args[0],color=bcolors.FAIL)
            printc(err.args[1],color=bcolors.FAIL)
            return        

        wavelength = 6173.3356

        for scan in range(int(data_shape[-1])):

            if isinstance(data_f, str):

                file_path = data_f

            elif isinstance(data_f, list):

                file_path = data_f[scan]
                wave_axis = wve_axis_arr[scan]

            #must invert each scan independently, as cmilos only takes in one dataset at a time

             #get wave_axis from the header information of the science scans

            shift_w =  wave_axis[3] - wavelength
            wave_axis = wave_axis - shift_w

            printc('   It is assumed the wavelength is given by the header info ')
            printc(wave_axis,color = bcolors.WARNING)
            printc((wave_axis - wavelength)*1000.,color = bcolors.WARNING)
            printc('   saving data into dummy_in.txt for RTE input')

            sdata = data[:,:,:,:,scan]
            y,x,p,l = sdata.shape
            print(y,x,p,l)

            filename = 'dummy_in.txt'
            with open(filename,"w") as f:
                for i in range(x):
                    for j in range(y):
                        for k in range(l):
                            f.write('%e %e %e %e %e \n' % (wave_axis[k],sdata[j,i,0,k],sdata[j,i,1,k],sdata[j,i,2,k],sdata[j,i,3,k])) #wv, I, Q, U, V
            del sdata

            printc(f'  ---- >>>>> Inverting data scan number: {scan} .... ',color=bcolors.OKGREEN)

            cmd = CMILOS_LOC+"./milos"
            cmd = fix_path(cmd)

            if rte == 'RTE':
                rte_on = subprocess.call(cmd+" 6 15 0 0 dummy_in.txt  >  dummy_out.txt",shell=True)
            if rte == 'CE':
                rte_on = subprocess.call(cmd+" 6 15 2 0 dummy_in.txt  >  dummy_out.txt",shell=True)
            if rte == 'CE+RTE':
                rte_on = subprocess.call(cmd+" 6 15 1 0 dummy_in.txt  >  dummy_out.txt",shell=True)

            #print(rte_on)

            printc('  ---- >>>>> Reading results.... ',color=bcolors.OKGREEN)
            del_dummy = subprocess.call("rm dummy_in.txt",shell=True)
            print(del_dummy)

            res = np.loadtxt('dummy_out.txt')
            npixels = res.shape[0]/12.
            print(npixels)
            print(npixels/x)
            result = np.zeros((12,y*x)).astype(float)
            rte_invs = np.zeros((12,y,x)).astype(float)
            for i in range(y*x):
                result[:,i] = res[i*12:(i+1)*12]
            result = result.reshape(12,y,x)
            result = np.einsum('ijk->ikj', result)
            rte_invs = result
            del result
            rte_invs_noth = np.copy(rte_invs)

            """
            From 0 to 11
            Counter (PX Id)
            Iterations
            Strength
            Inclination
            Azimuth
            Eta0 parameter
            Doppler width
            Damping
            Los velocity
            Constant source function
            Slope source function
            Minimum chisqr value
            """

            noise_in_V =  np.mean(data[:,:,3,cpos_arr[0],:])
            low_values_flags = np.max(np.abs(data[:,:,3,:,:]),axis=0) < noise_in_V  # Where values are low
            
            rte_invs[2,low_values_flags] = 0
            rte_invs[3,low_values_flags] = 0
            rte_invs[4,low_values_flags] = 0

            np.savez_compressed(out_dir+'_RTE', rte_invs=rte_invs, rte_invs_noth=rte_invs_noth)
            
            del_dummy = subprocess.call("rm dummy_out.txt",shell=True)
            print(del_dummy)

            rte_data_products = np.zeros((6,rte_invs_noth.shape[1],rte_invs_noth.shape[1]))

            rte_data_products[0,:,:] = rte_invs_noth[9,:,:] + rte_invs_noth[10,:,:] #continuum
            rte_data_products[1,:,:] = rte_invs_noth[2,:,:] #b mag strength
            rte_data_products[2,:,:] = rte_invs_noth[3,:,:] #inclination
            rte_data_products[3,:,:] = rte_invs_noth[4,:,:] #azimuth
            rte_data_products[4,:,:] = rte_invs_noth[8,:,:] #vlos
            rte_data_products[5,:,:] = rte_invs_noth[2,:,:]*np.cos(rte_invs_noth[3,:,:]*np.pi/180.) #blos

            with fits.open(file_path) as hdu_list:
                hdu_list[0].data = rte
                hdu_list.writeto(out_dir+str(file_path.split('.fits')[0][-10:])+'_rte_data_products.fits', overwrite=True)

            with fits.open(file_path) as hdu_list:
                hdu_list[0].data = rte_data_products[5,:,:]
                hdu_list.writeto(out_dir+str(file_path.split('.fits')[0][-10:])+'_blos_rte.fits', overwrite=True)

            with fits.open(file_path) as hdu_list:
                hdu_list[0].data = rte_data_products[4,:,:]
                hdu_list.writeto(out_dir+str(file_path.split('.fits')[0][-10:])+'_vlos_rte.fits', overwrite=True)

            with fits.open(file_path) as hdu_list:
                hdu_list[0].data = rte_data_products[0,:,:]
                hdu_list.writeto(out_dir+str(file_path.split('.fits')[0][-10:])+'_Icont_rte.fits', overwrite=True)

            printc('--------------------------------------------------------------',bcolors.OKGREEN)
            printc(f"------------- CMILOS RTE Run Time: {np.round(time.time() - start_time,3)} seconds ",bcolors.OKGREEN)
            printc('--------------------------------------------------------------',bcolors.OKGREEN)

    print(" ")
    printc('--------------------------------------------------------------',color=bcolors.OKGREEN)
    printc(f'------------ Reduction Complete: {np.round(time.time() - overall_time,3)} seconds',color=bcolors.OKGREEN)
    printc('--------------------------------------------------------------',color=bcolors.OKGREEN)


    return data

    
